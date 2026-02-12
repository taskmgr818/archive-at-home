package handler

import (
	"errors"
	"log"
	"net/http"

	"github.com/gin-gonic/gin"
	"github.com/gorilla/websocket"
	appctx "github.com/taskmgr818/archive-at-home/server/internal/context"
	"github.com/taskmgr818/archive-at-home/server/internal/model"
	"github.com/taskmgr818/archive-at-home/server/internal/node"
	"github.com/taskmgr818/archive-at-home/server/internal/service"
	"github.com/taskmgr818/archive-at-home/server/internal/store"
	"github.com/taskmgr818/archive-at-home/server/internal/ws"
)

// Handler holds HTTP/WS endpoint handlers.
type Handler struct {
	svc      *service.GalleryService
	hub      *ws.Hub
	store    *store.Store
	nodeAuth *node.Authenticator
	upgrader websocket.Upgrader
}

// NewHandler creates the handler set.
func NewHandler(svc *service.GalleryService, hub *ws.Hub, store *store.Store, nodeAuth *node.Authenticator) *Handler {
	return &Handler{
		svc:      svc,
		hub:      hub,
		store:    store,
		nodeAuth: nodeAuth,
		upgrader: websocket.Upgrader{
			ReadBufferSize:  1024,
			WriteBufferSize: 1024,
			CheckOrigin:     func(r *http.Request) bool { return true },
		},
	}
}

// RegisterRoutes registers all routes on the Gin engine.
// apiKeyMiddleware should be nil during early development (no-auth mode);
// when non-nil it protects all business endpoints via API key.
func (h *Handler) RegisterRoutes(r *gin.Engine, apiKeyMiddleware ...gin.HandlerFunc) {
	// ── Public endpoints (no auth) ──
	r.GET("/api/v1/health", h.Health)

	// ── WebSocket for worker nodes (uses its own node_id auth) ──
	r.GET("/ws", h.WebSocket)

	// ── Protected business endpoints ──
	api := r.Group("/api/v1")
	for _, mw := range apiKeyMiddleware {
		api.Use(mw)
	}
	{
		api.POST("/parse", h.ParseGallery)
	}
}

// ─────────────────────────────────────────────
// POST /api/v1/parse
// ─────────────────────────────────────────────

// ParseGallery handles gallery parse requests.
//
//	@Summary      Request gallery archive parsing
//	@Description  Checks cache (unless force=true), collapses duplicate requests,
//	              dispatches to worker nodes and returns the parsed result.
//	@Param        body  body  model.ParseRequest  true  "Parse request"
//	@Success      200   {object}  model.ParseResponse
//	@Failure      400
//	@Failure      429   "Quota exceeded"
//	@Failure      500
//	@Router       /api/v1/parse [post]
func (h *Handler) ParseGallery(c *gin.Context) {
	var req model.ParseRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}

	// UserID comes from the API key middleware, not the request body.
	userID := appctx.GetUserID(c)

	resp, err := h.svc.ParseGallery(c.Request.Context(), userID, &req)
	if err != nil {
		// Check for quota / balance exceeded
		if errors.Is(err, service.ErrInsufficientBalance) {
			c.JSON(http.StatusTooManyRequests, gin.H{"error": err.Error()})
			return
		}
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}

	c.JSON(http.StatusOK, resp)
}

// ─────────────────────────────────────────────
// GET /ws  (Worker node WebSocket)
// ─────────────────────────────────────────────

// WebSocket upgrades the connection and registers the worker node.
// Header: X-Auth-Token: <NodeID>:<Signature>
// Signature is ED25519 signed NodeID (Base64 encoded).
func (h *Handler) WebSocket(c *gin.Context) {
	// Extract and verify auth token from header
	authToken := c.GetHeader("X-Auth-Token")
	if authToken == "" {
		c.JSON(http.StatusUnauthorized, gin.H{"error": "X-Auth-Token header required"})
		return
	}

	// Verify signature and extract NodeID
	nodeID, err := h.nodeAuth.VerifyAuthToken(authToken)
	if err != nil {
		log.Printf("[handler] node auth failed: %v", err)
		c.JSON(http.StatusUnauthorized, gin.H{"error": "invalid authentication token"})
		return
	}

	// Upgrade to WebSocket
	conn, err := h.upgrader.Upgrade(c.Writer, c.Request, nil)
	if err != nil {
		log.Printf("[handler] websocket upgrade error: %v", err)
		return
	}

	// Register client and start listening
	client := ws.NewClient(nodeID, conn, h.hub)
	client.Run(c.Request.Context())
}

// ─────────────────────────────────────────────
// GET /api/v1/health
// ─────────────────────────────────────────────

// Health returns basic server health info.
func (h *Handler) Health(c *gin.Context) {
	c.JSON(http.StatusOK, gin.H{
		"status":          "ok",
		"connected_nodes": h.hub.ClientCount(),
	})
}
