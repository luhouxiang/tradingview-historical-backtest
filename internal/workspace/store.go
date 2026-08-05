package workspace

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"regexp"
	"sync"
	"time"

	"github.com/tvbt/tradingview-historical-backtest/internal/pythonclient"
	"github.com/tvbt/tradingview-historical-backtest/internal/storage"
)

var (
	ErrNotFound = errors.New("workspace document not found")
	ErrInvalid  = errors.New("invalid workspace document")
)

type ConflictError struct{ CurrentRevision int }

func (e *ConflictError) Error() string { return "workspace revision conflict" }

type Pane struct {
	ID        string  `json:"id"`
	Role      string  `json:"role"`
	Weight    float64 `json:"weight"`
	MinHeight int     `json:"min_height"`
	Visible   bool    `json:"visible"`
	Collapsed bool    `json:"collapsed"`
	Order     int     `json:"order"`
	Title     string  `json:"title,omitempty"`
}

type Panel struct {
	Width     int    `json:"width,omitempty"`
	Height    int    `json:"height,omitempty"`
	Collapsed bool   `json:"collapsed"`
	ActiveTab string `json:"active_tab"`
}

type ObjectOrder struct {
	ID          string `json:"id"`
	PaneID      string `json:"pane_id"`
	ZBand       int    `json:"z_band"`
	OrderInBand int    `json:"order_in_band"`
	Visible     bool   `json:"visible"`
	Locked      bool   `json:"locked"`
}

type IndicatorOutputStyle struct {
	Color     string  `json:"color"`
	LineWidth int     `json:"line_width"`
	LineStyle string  `json:"line_style"`
	Opacity   float64 `json:"opacity"`
	Visible   bool    `json:"visible"`
}

type IndicatorStyle struct {
	Outputs map[string]IndicatorOutputStyle `json:"outputs"`
}

type SeriesSource struct {
	SourceID     string                    `json:"source_id"`
	Name         string                    `json:"name"`
	PaneID       string                    `json:"pane_id"`
	Visible      bool                      `json:"visible"`
	Locked       bool                      `json:"locked"`
	ZBand        int                       `json:"z_band"`
	OrderInBand  int                       `json:"order_in_band"`
	DatasetID    string                    `json:"dataset_id"`
	DataRevision string                    `json:"data_revision"`
	Algorithm    pythonclient.AlgorithmRef `json:"algorithm"`
	Parameters   map[string]any            `json:"parameters"`
	Style        *IndicatorStyle           `json:"style,omitempty"`
}

type CategoryVisibility struct {
	Fractals        bool  `json:"fractals"`
	Bi              bool  `json:"bi"`
	Segments        *bool `json:"segments,omitempty"`
	Zhongshu        bool  `json:"zhongshu"`
	SegmentZhongshu *bool `json:"segment_zhongshu,omitempty"`
	MovementStates  *bool `json:"movement_states,omitempty"`
	CenterMonitors  *bool `json:"center_monitors,omitempty"`
	Divergences     *bool `json:"divergences,omitempty"`
	TradePoints     *bool `json:"trade_points,omitempty"`
}

type StrategySource struct {
	SourceID           string                    `json:"source_id"`
	Name               string                    `json:"name"`
	PaneID             string                    `json:"pane_id"`
	Visible            bool                      `json:"visible"`
	Locked             bool                      `json:"locked"`
	ZBand              int                       `json:"z_band"`
	OrderInBand        int                       `json:"order_in_band"`
	DatasetID          string                    `json:"dataset_id"`
	DataRevision       string                    `json:"data_revision"`
	Algorithm          pythonclient.AlgorithmRef `json:"algorithm"`
	Parameters         map[string]any            `json:"parameters"`
	CategoryVisibility CategoryVisibility        `json:"category_visibility"`
	Style              *IndicatorStyle           `json:"style,omitempty"`
}

type Layout struct {
	RequestID       string           `json:"request_id,omitempty"`
	SchemaVersion   int              `json:"schema_version"`
	LayoutID        string           `json:"layout_id"`
	ProfileID       string           `json:"profile_id"`
	Revision        int              `json:"revision"`
	Panes           []Pane           `json:"panes"`
	RightPanel      Panel            `json:"right_panel"`
	BottomPanel     Panel            `json:"bottom_panel"`
	ObjectOrder     []ObjectOrder    `json:"object_order"`
	SeriesSources   []SeriesSource   `json:"series_sources"`
	StrategySources []StrategySource `json:"strategy_sources"`
	UpdatedAt       time.Time        `json:"updated_at"`
}

type Anchor struct {
	Time       int64 `json:"time"`
	PriceI64   int64 `json:"price_i64"`
	PriceScale int64 `json:"price_scale"`
}

type DrawingStyle struct {
	Color       string  `json:"color"`
	LineWidth   float64 `json:"line_width"`
	FillOpacity float64 `json:"fill_opacity"`
}

type Drawing struct {
	ID          string       `json:"id"`
	Name        string       `json:"name"`
	Type        string       `json:"type"`
	PaneID      string       `json:"pane_id"`
	Visible     bool         `json:"visible"`
	Locked      bool         `json:"locked"`
	ZBand       int          `json:"z_band"`
	OrderInBand int          `json:"order_in_band"`
	Style       DrawingStyle `json:"style"`
	Anchors     []Anchor     `json:"anchors"`
	Text        string       `json:"text,omitempty"`
	Revision    int          `json:"revision"`
	CreatedAt   time.Time    `json:"created_at"`
	UpdatedAt   time.Time    `json:"updated_at"`
}

type Drawings struct {
	RequestID     string    `json:"request_id,omitempty"`
	SchemaVersion int       `json:"schema_version"`
	ProfileID     string    `json:"profile_id"`
	LayoutID      string    `json:"layout_id"`
	DatasetID     string    `json:"dataset_id"`
	DataRevision  string    `json:"data_revision"`
	Revision      int       `json:"revision"`
	Drawings      []Drawing `json:"drawings"`
	UpdatedAt     time.Time `json:"updated_at"`
}

type Store struct {
	guard *storage.PathGuard
	mu    sync.Mutex
}

func NewStore(guard *storage.PathGuard) *Store { return &Store{guard: guard} }

func (s *Store) GetLayout(profileID, layoutID string) (Layout, error) {
	if !validID(profileID) || !validID(layoutID) {
		return Layout{}, ErrInvalid
	}
	path, _ := s.guard.Resolve(fmt.Sprintf("workspaces/%s/layouts/%s.json", profileID, layoutID))
	var document Layout
	return document, read(path, &document)
}

func (s *Store) PutLayout(profileID, layoutID string, expected int, document Layout) (Layout, error) {
	if !validID(profileID) || !validID(layoutID) || document.SchemaVersion != 1 || document.ProfileID != profileID || document.LayoutID != layoutID {
		return Layout{}, ErrInvalid
	}
	if err := validateLayout(document); err != nil {
		return Layout{}, err
	}
	path, _ := s.guard.Resolve(fmt.Sprintf("workspaces/%s/layouts/%s.json", profileID, layoutID))
	s.mu.Lock()
	defer s.mu.Unlock()
	current, err := currentRevision[Layout](path)
	if err != nil {
		return Layout{}, err
	}
	if current != expected {
		return Layout{}, &ConflictError{CurrentRevision: current}
	}
	document.Revision = current + 1
	document.RequestID = ""
	document.UpdatedAt = time.Now().UTC()
	return document, write(path, document)
}

func (s *Store) GetDrawings(profileID, layoutID, datasetID string) (Drawings, error) {
	if !validID(profileID) || !validID(layoutID) || !validDatasetID(datasetID) {
		return Drawings{}, ErrInvalid
	}
	path, _ := s.guard.Resolve(fmt.Sprintf("workspaces/%s/drawings/%s/%s.json", profileID, layoutID, datasetID))
	var document Drawings
	return document, read(path, &document)
}

func (s *Store) PutDrawings(profileID, layoutID, datasetID string, expected int, document Drawings) (Drawings, error) {
	if !validID(profileID) || !validID(layoutID) || !validDatasetID(datasetID) || document.SchemaVersion != 1 || document.ProfileID != profileID || document.LayoutID != layoutID || document.DatasetID != datasetID || !regexp.MustCompile(`^sha256:[0-9a-f]{64}$`).MatchString(document.DataRevision) {
		return Drawings{}, ErrInvalid
	}
	for _, drawing := range document.Drawings {
		if err := validateDrawing(drawing); err != nil {
			return Drawings{}, err
		}
	}
	path, _ := s.guard.Resolve(fmt.Sprintf("workspaces/%s/drawings/%s/%s.json", profileID, layoutID, datasetID))
	s.mu.Lock()
	defer s.mu.Unlock()
	current, err := currentRevision[Drawings](path)
	if err != nil {
		return Drawings{}, err
	}
	if current != expected {
		return Drawings{}, &ConflictError{CurrentRevision: current}
	}
	document.Revision = current + 1
	document.RequestID = ""
	document.UpdatedAt = time.Now().UTC()
	return document, write(path, document)
}

func validateDrawing(drawing Drawing) error {
	validTypes := map[string]bool{"trend_line": true, "horizontal_line": true, "rectangle": true, "text": true, "measure": true}
	if !validID(drawing.ID) || drawing.Name == "" || !validTypes[drawing.Type] || drawing.PaneID == "" || drawing.ZBand != 600 || len(drawing.Anchors) < 1 || len(drawing.Anchors) > 2 || drawing.Style.LineWidth < 1 || drawing.Style.LineWidth > 8 || drawing.Style.FillOpacity < 0 || drawing.Style.FillOpacity > 1 || !regexp.MustCompile(`^#[0-9a-fA-F]{6}$`).MatchString(drawing.Style.Color) {
		return ErrInvalid
	}
	for _, anchor := range drawing.Anchors {
		if anchor.PriceScale < 1 {
			return ErrInvalid
		}
	}
	return nil
}

func validateLayout(document Layout) error {
	if document.Revision < 1 || len(document.Panes) == 0 || document.RightPanel.Width < 280 || document.RightPanel.Width > 600 || document.BottomPanel.Height < 160 {
		return ErrInvalid
	}
	if !member(document.RightPanel.ActiveTab, "watchlist", "object_tree", "data_window", "strategy_params") || !member(document.BottomPanel.ActiveTab, "replay", "backtest", "trades", "equity", "tasks", "logs") {
		return ErrInvalid
	}
	paneIDs := make(map[string]bool, len(document.Panes))
	orders := make(map[int]bool, len(document.Panes))
	pricePaneCount := 0
	for _, pane := range document.Panes {
		if !validID(pane.ID) || !member(pane.Role, "price", "indicator") || pane.Weight <= 0 || pane.MinHeight < 40 || pane.Order < 0 || paneIDs[pane.ID] || orders[pane.Order] {
			return ErrInvalid
		}
		paneIDs[pane.ID] = true
		orders[pane.Order] = true
		if pane.Role == "price" {
			pricePaneCount++
		}
	}
	if pricePaneCount != 1 {
		return ErrInvalid
	}
	zBands := map[int]bool{0: true, 100: true, 200: true, 300: true, 400: true, 500: true, 600: true, 700: true, 800: true, 900: true}
	objectIDs := make(map[string]bool, len(document.ObjectOrder))
	for _, object := range document.ObjectOrder {
		if !validID(object.ID) || !paneIDs[object.PaneID] || !zBands[object.ZBand] || object.OrderInBand < 0 || objectIDs[object.ID] {
			return ErrInvalid
		}
		objectIDs[object.ID] = true
	}
	sha256Pattern := regexp.MustCompile(`^sha256:[0-9a-f]{64}$`)
	for _, source := range document.SeriesSources {
		algorithm := source.Algorithm
		if !validID(source.SourceID) || source.Name == "" || !paneIDs[source.PaneID] || source.ZBand != 400 || source.OrderInBand < 0 || !validDatasetID(source.DatasetID) || !sha256Pattern.MatchString(source.DataRevision) || algorithm.Kind != "indicator" || algorithm.AlgorithmID == "" || algorithm.AlgorithmVersion == "" || !sha256Pattern.MatchString(algorithm.SourceHash) || source.Parameters == nil || objectIDs[source.SourceID] {
			return ErrInvalid
		}
		if err := validateIndicatorStyle(source.Style); err != nil {
			return err
		}
		objectIDs[source.SourceID] = true
	}
	for _, source := range document.StrategySources {
		algorithm := source.Algorithm
		if !validID(source.SourceID) || source.Name == "" || !paneIDs[source.PaneID] || source.ZBand != 500 || source.OrderInBand < 0 || !validDatasetID(source.DatasetID) || !sha256Pattern.MatchString(source.DataRevision) || algorithm.Kind != "chan" || algorithm.AlgorithmID == "" || algorithm.AlgorithmVersion == "" || !sha256Pattern.MatchString(algorithm.SourceHash) || source.Parameters == nil || objectIDs[source.SourceID] {
			return ErrInvalid
		}
		if err := validateIndicatorStyle(source.Style); err != nil {
			return err
		}
		objectIDs[source.SourceID] = true
	}
	return nil
}

func validateIndicatorStyle(style *IndicatorStyle) error {
	if style == nil {
		return nil
	}
	colorPattern := regexp.MustCompile(`^#[0-9a-fA-F]{6}$`)
	for output, value := range style.Outputs {
		if output == "" || !colorPattern.MatchString(value.Color) || value.LineWidth < 1 || value.LineWidth > 4 || !member(value.LineStyle, "solid", "dashed", "dotted") || value.Opacity < 0.1 || value.Opacity > 1 {
			return ErrInvalid
		}
	}
	return nil
}

func member(value string, options ...string) bool {
	for _, option := range options {
		if value == option {
			return true
		}
	}
	return false
}

func read[T any](path string, value *T) error {
	data, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return ErrNotFound
	}
	if err != nil {
		return err
	}
	if err := json.Unmarshal(data, value); err != nil {
		return fmt.Errorf("decode workspace: %w", err)
	}
	return nil
}

func write[T any](path string, value T) error {
	data, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	return storage.AtomicWriteFile(path, append(data, '\n'), 0o640)
}

func currentRevision[T interface{ Layout | Drawings }](path string) (int, error) {
	var document T
	if err := read(path, &document); errors.Is(err, ErrNotFound) {
		return 0, nil
	} else if err != nil {
		return 0, err
	}
	data, _ := json.Marshal(document)
	var header struct {
		Revision int `json:"revision"`
	}
	_ = json.Unmarshal(data, &header)
	return header.Revision, nil
}

var idPattern = regexp.MustCompile(`^[A-Za-z0-9_-]{1,128}$`)
var datasetPattern = regexp.MustCompile(`^[A-Za-z0-9_.-]{1,160}$`)

func validID(value string) bool { return idPattern.MatchString(value) }
func validDatasetID(value string) bool {
	return datasetPattern.MatchString(value) && value != "." && value != ".."
}
