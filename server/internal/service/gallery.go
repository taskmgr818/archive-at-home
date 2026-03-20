package service

import (
	"context"
	"errors"
	"fmt"
	"log"
	"time"

	"github.com/google/uuid"
	"github.com/taskmgr818/archive-at-home/server/internal/balance"
	"github.com/taskmgr818/archive-at-home/server/internal/config"
	"github.com/taskmgr818/archive-at-home/server/internal/model"
	"github.com/taskmgr818/archive-at-home/server/internal/scheduler"
	"github.com/taskmgr818/archive-at-home/server/internal/store"
	"github.com/taskmgr818/archive-at-home/server/internal/ws"
)

// Service errors
var (
	ErrInsufficientBalance = errors.New("insufficient balance")
)

// GalleryService orchestrates the full request lifecycle:
//
//	cache check → collapsing → publish → wait → return
type GalleryService struct {
	sched      *scheduler.Scheduler
	hub        *ws.Hub
	waiter     *ws.ResultWaiter
	store      *store.Store
	cfg        *config.Config
	balanceSvc balance.BalanceService
}

// NewGalleryService creates the service.
func NewGalleryService(
	sched *scheduler.Scheduler,
	hub *ws.Hub,
	waiter *ws.ResultWaiter,
	store *store.Store,
	cfg *config.Config,
	balanceSvc balance.BalanceService,
) *GalleryService {
	return &GalleryService{
		sched:      sched,
		hub:        hub,
		waiter:     waiter,
		store:      store,
		cfg:        cfg,
		balanceSvc: balanceSvc,
	}
}

// ParseGallery is the main business flow:
//
//  1. If force=false, check per-user cache → return if hit
//  2. Resolve params via e-hentai
//  3. Check request collapsing → reuse inflight if exists
//  4. Publish new task to Redis + broadcast to nodes
//  5. Block (async→sync) until result arrives or timeout
//
// userID is injected by the API key middleware (not from the request body).
func (s *GalleryService) ParseGallery(ctx context.Context, userID string, req *model.ParseRequest) (*model.ParseResponse, error) {
	// ── Step 1: Cache lookup (skip if force=true) ──
	if !req.Force {
		cached, err := s.sched.GetCachedResult(ctx, userID, req.GalleryID)
		if err != nil {
			log.Printf("[service] cache check error: %v", err)
			// continue – treat as miss
		}
		if cached != nil {
			log.Printf("[service] cache HIT user=%s gallery=%s", userID, req.GalleryID)
			return &model.ParseResponse{
				Cached:     true,
				ArchiveURL: *cached,
			}, nil
		}
	}

	// ── Step 2: Resolve params via e-hentai ──
	quota, err := ResolveParseParams(ctx, s.cfg, req.GalleryID, req.GalleryKey)
	if err != nil {
		return nil, fmt.Errorf("resolve e-hentai params: %w", err)
	}
	freeTier := quota.IsNew
	estimatedGP := quota.GP

	// ── Step 3: Generate traceID early for balance operations ──
	traceID := uuid.New().String()

	// ── Step 4: Balance check & freeze ──
	// Always charge users based on estimated GP
	if err := s.balanceSvc.FreezeGP(ctx, userID, traceID, int64(estimatedGP)); err != nil {
		if errors.Is(err, balance.ErrInsufficientBalance) {
			return nil, ErrInsufficientBalance
		}
		return nil, fmt.Errorf("freeze balance: %w", err)
	}
	log.Printf("[service] froze %d GP for user=%s trace=%s", estimatedGP, userID, traceID)

	// ── Step 5: Publish (with built-in collapsing) ──
	actualTraceID, created, err := s.sched.PublishTask(ctx, traceID, userID, req.GalleryID, req.GalleryKey, req.Force, freeTier, estimatedGP)
	if err != nil {
		// Refund frozen GP on publish failure
		if _, refundErr := s.balanceSvc.RefundTask(ctx, userID, traceID, int64(estimatedGP)); refundErr != nil {
			log.Printf("[service] refund error on publish failure: %v", refundErr)
		}
		return nil, fmt.Errorf("publish task: %w", err)
	}

	if created {
		log.Printf("[service] NEW task trace=%s user=%s gallery=%s key=%s force=%v free=%v estGP=%d",
			actualTraceID, userID, req.GalleryID, req.GalleryKey, req.Force, freeTier, estimatedGP)

		// Async SQL log
		s.store.LogTaskCreated(actualTraceID, userID, req.GalleryID, req.GalleryKey, req.Force, freeTier, estimatedGP)

		// Broadcast announcement to all worker nodes
		queueLen, _ := s.sched.PendingQueueLen(ctx)
		s.hub.BroadcastTaskAnnouncement(ctx, &model.TaskAnnouncement{
			TraceID:     actualTraceID,
			FreeTier:    freeTier,
			EstimatedGP: estimatedGP,
			QueueLen:    int(queueLen),
		})
	} else {
		log.Printf("[service] COLLAPSED into trace=%s user=%s gallery=%s",
			actualTraceID, userID, req.GalleryID)
		// Refund the frozen GP for the collapsed request's traceID,
		// since this goroutine will piggyback on the existing task.
		if _, refundErr := s.balanceSvc.RefundTask(ctx, userID, traceID, int64(estimatedGP)); refundErr != nil {
			log.Printf("[service] refund error on collapse: %v", refundErr)
		}
	}

	// ── Step 6: Wait for result (async → sync bridge) ──
	resultCh := s.waiter.Register(actualTraceID)
	defer s.waiter.Unregister(actualTraceID, resultCh)

	select {
	case result := <-resultCh:
		if result == nil {
			return &model.ParseResponse{
				Error: "task completed with nil result",
			}, nil
		}

		// Async SQL log
		s.store.LogTaskCompleted(actualTraceID, result.NodeID, result.Success, result.ActualGP)

		// Settle or refund balance (skip for collapsed requests – already refunded)
		if created {
			if result.Success {
				if _, err := s.balanceSvc.SettleTask(ctx, userID, actualTraceID, int64(estimatedGP), int64(result.ActualGP)); err != nil {
					log.Printf("[service] settle balance error: %v", err)
				} else {
					log.Printf("[service] settled task trace=%s frozen=%d actual=%d", actualTraceID, estimatedGP, result.ActualGP)
				}
			} else {
				if _, err := s.balanceSvc.RefundTask(ctx, userID, actualTraceID, int64(estimatedGP)); err != nil {
					log.Printf("[service] refund balance error: %v", err)
				} else {
					log.Printf("[service] refunded %d GP for failed task trace=%s", estimatedGP, actualTraceID)
				}
			}
		}

		if !result.Success {
			return &model.ParseResponse{
				Error: result.Error,
			}, nil
		}

		// Re-fetch from cache (the Lua script has written it)
		cached, _ := s.sched.GetCachedResult(ctx, userID, req.GalleryID)
		archiveURL := ""
		if cached != nil {
			archiveURL = *cached
		}
		gpCost := estimatedGP
		if !created {
			gpCost = 0 // collapsed request was not charged
		}
		return &model.ParseResponse{
			Cached:     false,
			GPCost:     gpCost,
			ArchiveURL: archiveURL,
		}, nil

	case <-time.After(s.cfg.TaskWaitTimeout):
		// Refund frozen GP on timeout (skip for collapsed – already refunded)
		if created {
			if _, err := s.balanceSvc.RefundTask(ctx, userID, actualTraceID, int64(estimatedGP)); err != nil {
				log.Printf("[service] refund balance error on timeout: %v", err)
			} else {
				log.Printf("[service] refunded %d GP for timeout trace=%s", estimatedGP, actualTraceID)
			}
		}
		return &model.ParseResponse{
			Error: "timeout waiting for node result",
		}, nil

	case <-ctx.Done():
		// Refund frozen GP on context cancellation (skip for collapsed – already refunded)
		if created {
			if _, err := s.balanceSvc.RefundTask(ctx, userID, actualTraceID, int64(estimatedGP)); err != nil {
				log.Printf("[service] refund balance error on cancellation: %v", err)
			}
		}
		return nil, ctx.Err()
	}
}
