package replay

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"

	"github.com/parquet-go/parquet-go"
	"github.com/tvbt/tradingview-historical-backtest/internal/storage"
)

type eventRow struct {
	EventSeq        int64  `parquet:"event_seq"`
	KnownAtBarIndex int64  `parquet:"known_at_bar_index"`
	ObjectType      string `parquet:"object_type"`
	ObjectID        string `parquet:"object_id"`
	Operation       string `parquet:"operation"`
	ObjectRevision  int64  `parquet:"object_revision"`
	PayloadJSON     string `parquet:"payload_json"`
}

type Event struct {
	EventSeq        int64          `json:"event_seq"`
	KnownAtBarIndex int64          `json:"known_at_bar_index"`
	ObjectType      string         `json:"object_type"`
	ObjectID        string         `json:"object_id"`
	Operation       string         `json:"operation"`
	ObjectRevision  int64          `json:"object_revision"`
	Payload         map[string]any `json:"payload"`
}

type EventResponse struct {
	ReplayID          string  `json:"replay_id"`
	KnownFromBarIndex int64   `json:"known_from_bar_index"`
	KnownToBarIndex   int64   `json:"known_to_bar_index"`
	EventCount        int     `json:"event_count"`
	Checksum          string  `json:"checksum"`
	Events            []Event `json:"events"`
}

func readEvents(guard *storage.PathGuard, replayID, key, ref string, from, to int64) (EventResponse, error) {
	directory, err := guard.Resolve(ref)
	if err != nil {
		return EventResponse{}, err
	}
	manifestData, err := os.ReadFile(filepath.Join(directory, "manifest.json"))
	if err != nil {
		return EventResponse{}, err
	}
	var manifest struct {
		CacheKey string `json:"cache_key"`
	}
	if json.Unmarshal(manifestData, &manifest) != nil || manifest.CacheKey != key {
		return EventResponse{}, fmt.Errorf("replay cache manifest mismatch")
	}
	rows, err := parquet.ReadFile[eventRow](filepath.Join(directory, "events.parquet"))
	if err != nil {
		return EventResponse{}, err
	}
	events := make([]Event, 0)
	for _, row := range rows {
		if row.KnownAtBarIndex < from || row.KnownAtBarIndex > to {
			continue
		}
		payload := map[string]any{}
		if row.Operation == "upsert" {
			if err := json.Unmarshal([]byte(row.PayloadJSON), &payload); err != nil {
				return EventResponse{}, err
			}
		}
		events = append(events, Event{row.EventSeq, row.KnownAtBarIndex, row.ObjectType, row.ObjectID, row.Operation, row.ObjectRevision, payload})
	}
	data, _ := json.Marshal(events)
	digest := sha256.Sum256(data)
	return EventResponse{ReplayID: replayID, KnownFromBarIndex: from, KnownToBarIndex: to, EventCount: len(events), Checksum: "sha256:" + hex.EncodeToString(digest[:]), Events: events}, nil
}
