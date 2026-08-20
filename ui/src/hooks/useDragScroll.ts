import { useRef, type PointerEvent, type MouseEvent } from 'react';
import {
  createDragScrollController,
  type DragScrollAxis,
  type DragScrollTarget,
} from '../utils/dragScroll';

/** Bind pointer drag-to-scroll onto an overflow container for kiosk touchscreens. */
export function useDragScroll<T extends HTMLElement>(axis: DragScrollAxis = 'y') {
  const ref = useRef<T>(null);
  const controllerRef = useRef<ReturnType<typeof createDragScrollController> | null>(null);

  if (controllerRef.current === null) {
    controllerRef.current = createDragScrollController(() => ref.current, { axis });
  }

  const controller = controllerRef.current;

  return {
    ref,
    onPointerDown: (event: PointerEvent<T>) =>
      controller.pointerDown({
        button: event.button,
        pointerId: event.pointerId,
        clientX: event.clientX,
        clientY: event.clientY,
        target: event.target as DragScrollTarget | null,
      }),
    onPointerMove: (event: PointerEvent<T>) =>
      controller.pointerMove({
        button: event.button,
        pointerId: event.pointerId,
        clientX: event.clientX,
        clientY: event.clientY,
        target: event.target as DragScrollTarget | null,
      }),
    onPointerUp: (event: PointerEvent<T>) => controller.pointerUp(event),
    onPointerCancel: (event: PointerEvent<T>) => controller.pointerCancel(event),
    onClickCapture: (event: MouseEvent<T>) => controller.clickCapture(event),
  };
}
