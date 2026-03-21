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
//	publish/collapse (with atomic cache check) → setup created task → wait → return
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

// setupCreatedTask resolves e-hentai params, freezes balance, and broadcasts.
// Returns (estimatedGP, error). On error the caller is responsible for cleanup.
func (s *GalleryService) setupCreatedTask(ctx context.Context, userID string, req *model.ParseRequest, traceID string) (int, error) {
	quota, err := ResolveParseParams(ctx, s.cfg, req.GalleryID, req.GalleryKey)
	if err != nil {
		return 0, fmt.Errorf("resolve e-hentai params: %w", err)
	}

	freeTier := quota.IsNew
	estimatedGP := quota.GP

	if err := s.balanceSvc.FreezeGP(ctx, userID, traceID, int64(estimatedGP)); err != nil {
		if errors.Is(err, balance.ErrInsufficientBalance) {
			return estimatedGP, ErrInsufficientBalance
		}
		return estimatedGP, fmt.Errorf("freeze balance: %w", err)
	}
	log.Printf("[service] froze %d GP for user=%s trace=%s", estimatedGP, userID, traceID)

	if err := s.sched.UpdateTaskCost(ctx, traceID, freeTier, estimatedGP); err != nil {
		// Balance already frozen — caller must refund.
		return estimatedGP, fmt.Errorf("update task metadata: %w", err)
	}

	log.Printf("[service] NEW task trace=%s user=%s gallery=%s key=%s force=%v free=%v estGP=%d",
		traceID, userID, req.GalleryID, req.GalleryKey, req.Force, freeTier, estimatedGP)

	// Async SQL log
	s.store.LogTaskCreated(traceID, userID, req.GalleryID, req.GalleryKey, req.Force, freeTier, estimatedGP)

	// Broadcast announcement to all worker nodes
	queueLen, _ := s.sched.PendingQueueLen(ctx)
	s.hub.BroadcastTaskAnnouncement(ctx, &model.TaskAnnouncement{
		TraceID:     traceID,
		FreeTier:    freeTier,
		EstimatedGP: estimatedGP,
		QueueLen:    int(queueLen),
	})

	return estimatedGP, nil
}

// ParseGallery is the main business flow:
//
//  1. Publish/collapse atomically (also checks cache in Lua)
//  2. If created: resolve params + freeze balance + broadcast
//  3. Block (async→sync) until result arrives or timeout
//
// userID is injected by the API key middleware (not from the request body).
func (s *GalleryService) ParseGallery(ctx context.Context, userID string, req *model.ParseRequest) (*model.ParseResponse, error) {
	// ── Step 1: Generate request trace and atomically publish/collapse ──
	traceID := uuid.New().String()
	status, payload, err := s.sched.PublishTask(ctx, traceID, userID, req.GalleryID, req.GalleryKey, req.Force)
	if err != nil {
		return nil, fmt.Errorf("publish task: %w", err)
	}

	if status == scheduler.PublishCached {
		log.Printf("[service] cache HIT user=%s gallery=%s", userID, req.GalleryID)
		return &model.ParseResponse{
			Cached:     true,
			ArchiveURL: payload,
		}, nil
	}

	actualTraceID := payload
	created := status == scheduler.PublishCreated
	resultCh := s.waiter.Register(actualTraceID)
	defer s.waiter.Unregister(actualTraceID, resultCh)

	estimatedGP := 0

	// ── Step 2: Setup created task (or log collapsed) ──
	if created {
		estimatedGP, err = s.setupCreatedTask(ctx, userID, req, actualTraceID)
		if err != nil {
			// Cancel the Redis task
			if cancelErr := s.sched.CancelTask(ctx, actualTraceID, userID, req.GalleryID); cancelErr != nil {
				log.Printf("[service] cancel task error trace=%s: %v", actualTraceID, cancelErr)
			}
			// Notify any collapsed waiters that the task failed
			s.waiter.Notify(actualTraceID, &model.TaskResult{
				TraceID: actualTraceID,
				Success: false,
				Error:   err.Error(),
			})
			// Refund if balance was frozen (FreezeGP succeeded but later step failed)
			if !errors.Is(err, ErrInsufficientBalance) && estimatedGP > 0 {
				if _, refundErr := s.balanceSvc.RefundTask(ctx, userID, actualTraceID, int64(estimatedGP)); refundErr != nil {
					log.Printf("[service] refund balance error for setup failure: %v", refundErr)
				}
			}
			if errors.Is(err, ErrInsufficientBalance) {
				return nil, ErrInsufficientBalance
			}
			return nil, err
		}
	} else {
		log.Printf("[service] COLLAPSED into trace=%s user=%s gallery=%s",
			actualTraceID, userID, req.GalleryID)
	}

	// ── Step 3: Wait for result (async → sync bridge) ──
	//
	// Only created tasks have frozen GP to settle/refund.
	refund := func(reason string) {
		if !created || estimatedGP == 0 {
			return
		}
		if _, err := s.balanceSvc.RefundTask(ctx, userID, actualTraceID, int64(estimatedGP)); err != nil {
			log.Printf("[service] refund balance error %s: %v", reason, err)
			return
		}
		log.Printf("[service] refunded %d GP %s trace=%s", estimatedGP, reason, actualTraceID)
	}

	select {
	case result := <-resultCh:
		if result == nil {
			refund("for nil result")
			return &model.ParseResponse{Error: "task completed with nil result"}, nil
		}

		// Async SQL log
		s.store.LogTaskCompleted(actualTraceID, result.NodeID, result.Success, result.ActualGP)

		if created {
			if result.Success {
				if _, err := s.balanceSvc.SettleTask(ctx, userID, actualTraceID, int64(estimatedGP), int64(result.ActualGP)); err != nil {
					log.Printf("[service] settle balance error: %v", err)
				} else {
					log.Printf("[service] settled task trace=%s frozen=%d actual=%d", actualTraceID, estimatedGP, result.ActualGP)
				}
			} else {
				refund("for failed task")
			}
		}

		if !result.Success {
			return &model.ParseResponse{Error: result.Error}, nil
		}

		gpCost := estimatedGP
		if !created {
			gpCost = 0 // collapsed request was not charged
		}
		return &model.ParseResponse{
			Cached:     false,
			GPCost:     gpCost,
			ArchiveURL: result.ArchiveURL,
		}, nil

	case <-time.After(s.cfg.TaskWaitTimeout):
		refund("for timeout")
		return &model.ParseResponse{Error: "timeout waiting for node result"}, nil

	case <-ctx.Done():
		refund("on cancellation")
		return nil, ctx.Err()
	}
}
