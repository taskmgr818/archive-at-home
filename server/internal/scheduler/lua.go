package scheduler

// ─────────────────────────────────────────────
// Lua Scripts for Atomic Redis Operations
// ─────────────────────────────────────────────

// LuaFetchTask atomically claims a PENDING task for a worker node.
//
// KEYS[1] = task:{traceID}          (hash)
// ARGV[1] = nodeID
// ARGV[2] = leaseTTL (seconds)
//
// Returns:
//
//	[1]  "OK"        – task successfully claimed
//	[1]  "GONE"      – task already claimed or doesn't exist
//	+ task fields    – gallery_id, gallery_key (when OK)
const LuaFetchTask = `
local taskKey = KEYS[1]
local nodeID  = ARGV[1]
local leaseTTL = tonumber(ARGV[2])

-- 1. Check task exists and is still PENDING
local status = redis.call("HGET", taskKey, "status")
if status ~= "PENDING" then
    return {"GONE"}
end

-- 2. Atomically set to PROCESSING and bind node
redis.call("HMSET", taskKey,
    "status",  "PROCESSING",
    "node_id", nodeID
)

-- 3. Set lease TTL – if not reported back within this window, key expires
--    and lease watchdog can re-enqueue.
redis.call("EXPIRE", taskKey, leaseTTL)

-- 4. Return task details needed by the node
local fields = redis.call("HMGET", taskKey, "gallery_id", "gallery_key")
return {"OK", fields[1], fields[2]}
`

// LuaCompleteTask atomically marks a task as COMPLETED and stores
// the result in the per-user cache key.
//
// KEYS[1] = task:{traceID}               (hash)
// KEYS[2] = cache:{userID}:{galleryID}   (string – archive URL)
// KEYS[3] = inflight:{userID}:{galleryID}(collapsing key)
// KEYS[4] = queue:pending                (list)
// ARGV[1] = archive URL
// ARGV[2] = cacheTTL (seconds)
// ARGV[3] = nodeID (requesting node)
// ARGV[4] = actualGP
//
// Returns: "OK", "INVALID", or "NODE_MISMATCH"
const LuaCompleteTask = `
local taskKey      = KEYS[1]
local cacheKey     = KEYS[2]
local collapseKey  = KEYS[3]
local queueKey     = KEYS[4]
local archiveURL   = ARGV[1]
local cacheTTL     = tonumber(ARGV[2])
local nodeID       = ARGV[3]
local actualGP     = ARGV[4]

local status = redis.call("HGET", taskKey, "status")
if status ~= "PROCESSING" then
    return "INVALID"
end

-- Verify the task is still assigned to this node (prevent stale completion)
local assignedNode = redis.call("HGET", taskKey, "node_id")
if assignedNode ~= nodeID then
    return "NODE_MISMATCH"
end

-- 1. Mark task done and record actual GP
redis.call("HMSET", taskKey, "status", "COMPLETED", "actual_gp", actualGP)
redis.call("EXPIRE", taskKey, 300)  -- keep metadata 5 min for diagnostics

-- 2. Store result in per-user cache with 7-day TTL
redis.call("SET", cacheKey, archiveURL, "EX", cacheTTL)

-- 3. Remove collapsing key so future requests create new tasks
redis.call("DEL", collapseKey)

-- 4. Remove completed task from pending queue
local traceID = redis.call("HGET", taskKey, "trace_id")
redis.call("LREM", queueKey, 0, traceID)

return "OK"
`

// LuaPublishTask creates a new task hash if no inflight task exists
// for the same user+gallery (request collapsing).
//
// KEYS[1] = task:{traceID}                (hash to create)
// KEYS[2] = inflight:{userID}:{galleryID} (collapsing sentinel)
// KEYS[3] = queue:pending                 (list)
// ARGV[1] = traceID
// ARGV[2] = userID
// ARGV[3] = galleryID
// ARGV[4] = force   ("0" or "1")
// ARGV[5] = leaseTTL (seconds) – used for inflight key expiry
// ARGV[6] = galleryKey
// ARGV[7] = freeTier ("0" or "1")
// ARGV[8] = estimatedGP
//
// Returns:
//
//	"CREATED"   – new task created
//	traceID     – existing inflight task (collapsed)
const LuaPublishTask = `
local taskKey      = KEYS[1]
local collapseKey  = KEYS[2]
local queueKey     = KEYS[3]
local traceID      = ARGV[1]
local userID       = ARGV[2]
local galleryID    = ARGV[3]
local force        = ARGV[4]
local leaseTTL     = tonumber(ARGV[5])
local galleryKey   = ARGV[6]
local freeTier     = ARGV[7]
local estimatedGP  = ARGV[8]

-- Request Collapsing: check if an identical task is already in-flight
local existing = redis.call("GET", collapseKey)
if existing then
    -- Verify the task still exists (avoid collapsing to expired tasks)
    local existingTaskKey = "task:" .. existing
    local existingStatus = redis.call("HGET", existingTaskKey, "status")
    if existingStatus == "PENDING" or existingStatus == "PROCESSING" then
        return existing  -- return the traceID of the existing task
    end
    -- Task expired, clear stale collapseKey and create new task
    redis.call("DEL", collapseKey)
end

-- Create the task hash
redis.call("HMSET", taskKey,
    "trace_id",      traceID,
    "user_id",       userID,
    "gallery_id",    galleryID,
    "gallery_key",   galleryKey,
    "status",        "PENDING",
    "force",         force,
    "free_tier",     freeTier,
    "estimated_gp",  estimatedGP,
    "actual_gp",     "0",
    "node_id",       ""
)
redis.call("EXPIRE", taskKey, leaseTTL * 3)  -- generous TTL for the hash itself

-- Set collapsing sentinel
redis.call("SET", collapseKey, traceID, "EX", leaseTTL * 2)

-- Push to pending queue
redis.call("RPUSH", queueKey, traceID)

return "CREATED"
`

// LuaReclaimTask handles timeout/failure recovery:
// - Resets PROCESSING tasks back to PENDING
// - Re-sets the collapsing key to protect from duplication
// - Re-enqueues the task
//
// KEYS[1] = task:{traceID}                (hash)
// KEYS[2] = inflight:{userID}:{galleryID} (collapsing key)
// KEYS[3] = queue:pending                 (list)
// ARGV[1] = leaseTTL (seconds)
//
// Returns:
//
//	"RECLAIMED"    – task was reset and re-enqueued
//	"NOT_NEEDED"   – task is not in PROCESSING state or doesn't exist
const LuaReclaimTask = `
local taskKey      = KEYS[1]
local collapseKey  = KEYS[2]
local queueKey     = KEYS[3]
local leaseTTL     = tonumber(ARGV[1])

-- Check if task exists and is PROCESSING
local status = redis.call("HGET", taskKey, "status")
if status ~= "PROCESSING" then
    return "NOT_NEEDED"
end

-- Reset to PENDING and clear node assignment
redis.call("HMSET", taskKey,
    "status",  "PENDING",
    "node_id", ""
)
redis.call("EXPIRE", taskKey, leaseTTL * 3)

-- Re-enqueue first
local traceID = redis.call("HGET", taskKey, "trace_id")
redis.call("RPUSH", queueKey, traceID)

-- Re-set collapsing key to protect the re-enqueued task from duplication
redis.call("SET", collapseKey, traceID, "EX", leaseTTL * 2)

return "RECLAIMED"
`
