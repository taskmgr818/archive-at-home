package scheduler

import (
	"context"
	"fmt"
	"log"
	"time"

	"github.com/redis/go-redis/v9"
	"github.com/taskmgr818/archive-at-home/server/internal/config"
	"github.com/taskmgr818/archive-at-home/server/internal/model"
)

// Scheduler manages task lifecycle via Redis.
type Scheduler struct {
	rdb *redis.Client
	cfg *config.Config

	// Pre-loaded Lua script SHAs
	fetchScript    *redis.Script
	completeScript *redis.Script
	publishScript  *redis.Script
	reclaimScript  *redis.Script
}

// NewScheduler initialises the scheduler and loads Lua scripts.
func NewScheduler(rdb *redis.Client, cfg *config.Config) *Scheduler {
	return &Scheduler{
		rdb:            rdb,
		cfg:            cfg,
		fetchScript:    redis.NewScript(LuaFetchTask),
		completeScript: redis.NewScript(LuaCompleteTask),
		publishScript:  redis.NewScript(LuaPublishTask),
		reclaimScript:  redis.NewScript(LuaReclaimTask),
	}
}

// ─────────────────────────────────────────────
// Public API
// ─────────────────────────────────────────────

// PublishTask creates a new task or collapses into an existing one.
// Returns the traceID (may be a different one if collapsed) and whether it was newly created.
func (s *Scheduler) PublishTask(ctx context.Context, traceID, userID, galleryID, galleryKey string, force, freeTier bool, estimatedGP int) (string, bool, error) {
	forceFlag := "0"
	if force {
		forceFlag = "1"
	}
	freeTierFlag := "0"
	if freeTier {
		freeTierFlag = "1"
	}
	leaseTTL := int(s.cfg.TaskLeaseTTL.Seconds())

	keys := []string{
		model.TaskKey(traceID),
		model.CollapsingKey(userID, galleryID),
		model.PendingQueueKey,
	}
	args := []interface{}{traceID, userID, galleryID, forceFlag, leaseTTL, galleryKey, freeTierFlag, estimatedGP}

	result, err := s.publishScript.Run(ctx, s.rdb, keys, args...).Text()
	if err != nil {
		return "", false, fmt.Errorf("publish task lua: %w", err)
	}

	if result == "CREATED" {
		return traceID, true, nil
	}
	// Collapsed into existing task – result is the existing traceID
	return result, false, nil
}

// FetchTask lets a worker node attempt to claim a pending task.
// Returns the assignment details or an indication that the task is gone.
func (s *Scheduler) FetchTask(ctx context.Context, traceID, nodeID string) (*model.TaskAssignment, error) {
	leaseTTL := int(s.cfg.TaskLeaseTTL.Seconds())

	keys := []string{model.TaskKey(traceID)}
	args := []interface{}{nodeID, leaseTTL}

	vals, err := s.fetchScript.Run(ctx, s.rdb, keys, args...).StringSlice()
	if err != nil {
		return nil, fmt.Errorf("fetch task lua: %w", err)
	}

	if len(vals) == 0 || vals[0] == "GONE" {
		return nil, nil // task already claimed
	}

	// vals = ["OK", galleryID, galleryKey]
	return &model.TaskAssignment{
		TraceID:    traceID,
		GalleryID:  vals[1],
		GalleryKey: vals[2],
	}, nil
}

// CompleteTask stores the result and updates caches.
// nodeID must match the node currently assigned to the task.
func (s *Scheduler) CompleteTask(ctx context.Context, traceID, nodeID, archiveURL string, actualGP int) error {
	// Look up task metadata to build cache key
	taskKey := model.TaskKey(traceID)
	pipe := s.rdb.Pipeline()
	userIDCmd := pipe.HGet(ctx, taskKey, "user_id")
	galleryIDCmd := pipe.HGet(ctx, taskKey, "gallery_id")
	_, err := pipe.Exec(ctx)
	if err != nil {
		return fmt.Errorf("get task metadata: %w", err)
	}
	userID := userIDCmd.Val()
	galleryID := galleryIDCmd.Val()

	cacheTTL := int(s.cfg.CacheTTL.Seconds())

	keys := []string{
		taskKey,
		model.CacheKey(userID, galleryID),
		model.CollapsingKey(userID, galleryID),
		model.PendingQueueKey,
	}
	args := []interface{}{archiveURL, cacheTTL, nodeID, actualGP}

	status, err := s.completeScript.Run(ctx, s.rdb, keys, args...).Text()
	if err != nil {
		return fmt.Errorf("complete task lua: %w", err)
	}
	if status == "NODE_MISMATCH" {
		return fmt.Errorf("task reassigned to another node (stale completion attempt)")
	}
	if status != "OK" {
		return fmt.Errorf("complete task: unexpected status %s", status)
	}
	return nil
}

// GetCachedResult checks the per-user cache.
func (s *Scheduler) GetCachedResult(ctx context.Context, userID, galleryID string) (*string, error) {
	data, err := s.rdb.Get(ctx, model.CacheKey(userID, galleryID)).Result()
	if err == redis.Nil {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &data, nil
}

// PendingQueueLen returns the current length of the pending queue.
func (s *Scheduler) PendingQueueLen(ctx context.Context) (int64, error) {
	return s.rdb.LLen(ctx, model.PendingQueueKey).Result()
}

// ─────────────────────────────────────────────
// Lease Watchdog (background goroutine)
// ─────────────────────────────────────────────

// StartLeaseWatchdog periodically scans for expired task keys
// whose lease TTL has passed and re-enqueues them.
// It runs until ctx is cancelled.
func (s *Scheduler) StartLeaseWatchdog(ctx context.Context) {
	ticker := time.NewTicker(30 * time.Second)
	defer ticker.Stop()

	log.Println("[scheduler] lease watchdog started")
	for {
		select {
		case <-ctx.Done():
			log.Println("[scheduler] lease watchdog stopped")
			return
		case <-ticker.C:
			s.reclaimExpiredTasks(ctx)
		}
	}
}

// reclaimExpiredTasks scans for stuck PROCESSING tasks and:
// 1. Checks if task is still in PROCESSING state with low TTL
// 2. Calls LuaReclaimTask to reset, clear collapseKey, and re-enqueue
// 3. Removes truly expired tasks from queue
func (s *Scheduler) reclaimExpiredTasks(ctx context.Context) {
	queueLen, err := s.rdb.LLen(ctx, model.PendingQueueKey).Result()
	if err != nil || queueLen == 0 {
		return
	}

	// Scan up to 100 entries
	limit := queueLen
	if limit > 100 {
		limit = 100
	}

	leaseTTL := int(s.cfg.TaskLeaseTTL.Seconds())
	reclaimThreshold := leaseTTL / 2 // Reclaim if TTL < 50% of lease

	for i := int64(0); i < limit; i++ {
		traceID, err := s.rdb.LIndex(ctx, model.PendingQueueKey, i).Result()
		if err != nil {
			continue
		}
		taskKey := model.TaskKey(traceID)

		// Check if task exists
		pipe := s.rdb.Pipeline()
		ttlCmd := pipe.TTL(ctx, taskKey)
		statusCmd := pipe.HGet(ctx, taskKey, "status")
		userIDCmd := pipe.HGet(ctx, taskKey, "user_id")
		galleryIDCmd := pipe.HGet(ctx, taskKey, "gallery_id")
		_, err = pipe.Exec(ctx)
		if err != nil {
			// Task doesn't exist – remove from queue
			s.rdb.LRem(ctx, model.PendingQueueKey, 1, traceID)
			log.Printf("[scheduler] removed expired task %s from queue", traceID)
			continue
		}

		ttl := ttlCmd.Val().Seconds()
		status := statusCmd.Val()

		// If task is PROCESSING and TTL is low, reclaim it
		if status == "PROCESSING" && ttl > 0 && ttl < float64(reclaimThreshold) {
			userID := userIDCmd.Val()
			galleryID := galleryIDCmd.Val()

			keys := []string{
				taskKey,
				model.CollapsingKey(userID, galleryID),
				model.PendingQueueKey,
			}
			args := []interface{}{leaseTTL}

			result, err := s.reclaimScript.Run(ctx, s.rdb, keys, args...).Text()
			if err != nil {
				log.Printf("[scheduler] reclaim task %s error: %v", traceID, err)
				continue
			}
			if result == "RECLAIMED" {
				log.Printf("[scheduler] reclaimed stuck task %s (TTL was %.0fs)", traceID, ttl)
			}
		}
	}
}
