package ws

import (
	"context"
	"encoding/json"
	"log"
	"time"

	"github.com/gorilla/websocket"
	"github.com/taskmgr818/archive-at-home/server/internal/model"
)

const (
	// Time allowed to write a message to the peer.
	writeWait = 10 * time.Second

	// Time allowed to read the next pong message from the peer.
	pongWait = 60 * time.Second

	// Send pings to peer with this period. Must be less than pongWait.
	pingPeriod = (pongWait * 9) / 10

	// Maximum message size allowed from peer.
	maxMessageSize = 1 << 20 // 1 MB

	// Send buffer size
	sendBufSize = 256
)

// Client represents a single WebSocket connection from a worker node.
type Client struct {
	NodeID string
	conn   *websocket.Conn
	hub    *Hub
	send   chan []byte
}

// NewClient wraps a WebSocket connection.
func NewClient(nodeID string, conn *websocket.Conn, hub *Hub) *Client {
	return &Client{
		NodeID: nodeID,
		conn:   conn,
		hub:    hub,
		send:   make(chan []byte, sendBufSize),
	}
}

// Run starts read and write pumps. Blocks until the connection closes.
func (c *Client) Run(ctx context.Context) {
	c.hub.Register(c)
	go c.writePump()
	c.readPump(ctx) // blocks
	c.hub.Unregister(c)
}

// ─────────────────────────────────────────────
// Read pump: Node → Server
// ─────────────────────────────────────────────

func (c *Client) readPump(ctx context.Context) {
	defer c.conn.Close()

	c.conn.SetReadLimit(maxMessageSize)
	c.conn.SetReadDeadline(time.Now().Add(pongWait))
	c.conn.SetPongHandler(func(string) error {
		c.conn.SetReadDeadline(time.Now().Add(pongWait))
		return nil
	})

	for {
		_, message, err := c.conn.ReadMessage()
		if err != nil {
			if websocket.IsUnexpectedCloseError(err,
				websocket.CloseGoingAway, websocket.CloseNormalClosure) {
				log.Printf("[ws] node %s read error: %v", c.NodeID, err)
			}
			return
		}
		c.handleMessage(ctx, message)
	}
}

func (c *Client) handleMessage(ctx context.Context, raw []byte) {
	var env struct {
		Type    model.MsgType   `json:"type"`
		Payload json.RawMessage `json:"payload"`
	}
	if err := json.Unmarshal(raw, &env); err != nil {
		log.Printf("[ws] node %s: invalid message: %v", c.NodeID, err)
		return
	}

	switch env.Type {
	case model.MsgTypeFetchTask:
		var req model.FetchTaskRequest
		if err := json.Unmarshal(env.Payload, &req); err != nil {
			log.Printf("[ws] node %s: bad FETCH_TASK payload: %v", c.NodeID, err)
			return
		}
		req.NodeID = c.NodeID // enforce server-side node identity
		c.hub.HandleFetchTask(ctx, c, &req)

	case model.MsgTypeTaskResult:
		var res model.TaskResult
		if err := json.Unmarshal(env.Payload, &res); err != nil {
			log.Printf("[ws] node %s: bad TASK_RESULT payload: %v", c.NodeID, err)
			return
		}
		res.NodeID = c.NodeID
		c.hub.HandleTaskResult(ctx, c, &res)

	default:
		log.Printf("[ws] node %s: unknown message type: %s", c.NodeID, env.Type)
	}
}

// ─────────────────────────────────────────────
// Write pump: Server → Node
// ─────────────────────────────────────────────

func (c *Client) writePump() {
	ticker := time.NewTicker(pingPeriod)
	defer func() {
		ticker.Stop()
		c.conn.Close()
	}()

	for {
		select {
		case message, ok := <-c.send:
			c.conn.SetWriteDeadline(time.Now().Add(writeWait))
			if !ok {
				// Hub closed the channel
				c.conn.WriteMessage(websocket.CloseMessage, []byte{})
				return
			}

			w, err := c.conn.NextWriter(websocket.TextMessage)
			if err != nil {
				return
			}
			w.Write(message)

			// Batch queued messages into a single write if possible
			n := len(c.send)
			for i := 0; i < n; i++ {
				w.Write([]byte("\n"))
				w.Write(<-c.send)
			}

			if err := w.Close(); err != nil {
				return
			}

		case <-ticker.C:
			c.conn.SetWriteDeadline(time.Now().Add(writeWait))
			if err := c.conn.WriteMessage(websocket.PingMessage, nil); err != nil {
				return
			}
		}
	}
}
