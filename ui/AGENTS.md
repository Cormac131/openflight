# OpenFlight UI — agent notes

The production UI is a **1024×600 Raspberry Pi kiosk**. Assume a finger, not a
mouse. `*` is `touch-action: manipulation`, so native overflow scrolling often
does not work; Pi displays also report finger motion as mouse drags.

## Overflow + tap (always consider)

Any list, chip row, or other region that can overflow **must**:

1. Scroll by dragging on the content (not only via a scrollbar).
2. Still allow selecting a **single** item with a tap.

Do not put unbounded chip rows in `PanelHeader` actions. They clip the title.
Put them in the panel body (above tiles/lists) with a single-line horizontal
scroller. Keep a pinned control like **All** outside that scroller so it never
scrolls away.

### How

Reuse `useDragScroll` / `createDragScrollController` (`src/hooks/useDragScroll.ts`,
`src/utils/dragScroll.ts`):

- Vertical lists: `useDragScroll()` (axis `y`). See `ShotsPanel`.
- Horizontal chip rows: `useDragScroll('x')`. See `StatsPanel`.
- Capture the pointer **only after** `DRAG_SCROLL_THRESHOLD_PX` (16px) so a
  tap still clicks. After a real drag, `onClickCapture` swallows the click so
  the item under the finger is not selected.
- Set `touch-action: none` on the scroller **and** its tappable children
  (`button`, row mains, chips).

### Tests

- Unit-test drag vs tap on the controller (threshold, axis, click suppress).
- E2E: mouse-drag must scroll without activating the item; `hasTouch: true`
  must still tap-select one item.
