/**
 * Long-press to drag floating action buttons (WhatsApp launcher + AI chatbot).
 * Tap/click without drag keeps normal behaviour (navigate / open chat).
 */
(function () {
  'use strict';

  const LONG_PRESS_MS = 480;
  const MOVE_CANCEL_PX = 12;
  const DRAG_THRESHOLD_PX = 8;
  const FAB_SIZE = 58;
  const PADDING = 10;

  function clamp(val, min, max) {
    return Math.max(min, Math.min(max, val));
  }

  function pointerXY(e) {
    if (e.touches && e.touches.length) return { x: e.touches[0].clientX, y: e.touches[0].clientY };
    return { x: e.clientX, y: e.clientY };
  }

  function initFabDrag(options) {
    const container = typeof options.container === 'string'
      ? document.querySelector(options.container)
      : options.container;
    const handle = typeof options.handle === 'string'
      ? document.querySelector(options.handle)
      : (options.handle || container);
    if (!container || !handle) return;

    const storageKey = options.storageKey;
    const axis = options.axis || 'right'; // 'left' | 'right'
    const onTap = options.onTap || null;

    let pressTimer = null;
    let dragActive = false;
    let dragMoved = false;
    let suppressClick = false;
    let startX = 0;
    let startY = 0;
    let initialSide = 0;
    let initialBottom = 0;

    function applyPosition(side, bottom) {
      container.style.bottom = bottom + 'px';
      container.setAttribute('data-fab-positioned', 'true');
      if (axis === 'left') {
        container.style.left = side + 'px';
        container.style.right = 'auto';
      } else {
        container.style.right = side + 'px';
        container.style.left = 'auto';
      }
    }

    function loadSaved() {
      if (!storageKey) return;
      try {
        const raw = localStorage.getItem(storageKey);
        if (!raw) return;
        const pos = JSON.parse(raw);
        const side = clamp(
          Number(pos.side) || (axis === 'left' ? 30 : 30),
          PADDING,
          window.innerWidth - FAB_SIZE - PADDING
        );
        const bottom = clamp(
          Number(pos.bottom) || 30,
          PADDING,
          window.innerHeight - FAB_SIZE - PADDING
        );
        applyPosition(side, bottom);
      } catch (e) {
        console.warn('FAB position restore failed', e);
      }
    }

    function savePosition() {
      if (!storageKey || !dragMoved) return;
      const style = window.getComputedStyle(container);
      const side = axis === 'left'
        ? parseInt(style.left, 10)
        : parseInt(style.right, 10);
      const bottom = parseInt(style.bottom, 10);
      localStorage.setItem(storageKey, JSON.stringify({ side, bottom, axis }));
    }

    function clearPressTimer() {
      if (pressTimer) {
        clearTimeout(pressTimer);
        pressTimer = null;
      }
    }

    function beginDragMode() {
      dragActive = true;
      dragMoved = false;
      handle.classList.add('fab-is-dragging');
      container.classList.add('fab-container-dragging');
      document.body.classList.add('fab-drag-active');
      if (navigator.vibrate) navigator.vibrate(35);

      const style = window.getComputedStyle(container);
      initialSide = axis === 'left'
        ? parseInt(style.left, 10) || 30
        : parseInt(style.right, 10) || 30;
      initialBottom = parseInt(style.bottom, 10) || 30;
      container.style.animation = 'none';
      container.style.transition = 'none';
    }

    function onPointerDown(e) {
      if (e.type === 'mousedown' && e.button !== 0) return;
      const p = pointerXY(e);
      startX = p.x;
      startY = p.y;
      dragActive = false;
      dragMoved = false;
      suppressClick = false;

      clearPressTimer();
      pressTimer = setTimeout(function () {
        pressTimer = null;
        beginDragMode();
      }, LONG_PRESS_MS);
    }

    function onPointerMove(e) {
      const p = pointerXY(e);
      const dx0 = p.x - startX;
      const dy0 = p.y - startY;

      if (!dragActive) {
        if (Math.abs(dx0) > MOVE_CANCEL_PX || Math.abs(dy0) > MOVE_CANCEL_PX) {
          clearPressTimer();
        }
        return;
      }

      if (e.cancelable) e.preventDefault();

      const deltaX = startX - p.x;
      const deltaY = startY - p.y;

      if (!dragMoved && (Math.abs(deltaX) > DRAG_THRESHOLD_PX || Math.abs(deltaY) > DRAG_THRESHOLD_PX)) {
        dragMoved = true;
      }

      if (dragMoved) {
        let newSide = initialSide + (axis === 'left' ? -deltaX : deltaX);
        let newBottom = initialBottom + deltaY;
        newSide = clamp(newSide, PADDING, window.innerWidth - FAB_SIZE - PADDING);
        newBottom = clamp(newBottom, PADDING, window.innerHeight - FAB_SIZE - PADDING);
        applyPosition(newSide, newBottom);
      }
    }

    function endPointerSession() {
      clearPressTimer();
      if (dragActive) {
        dragActive = false;
        handle.classList.remove('fab-is-dragging');
        container.classList.remove('fab-container-dragging');
        document.body.classList.remove('fab-drag-active');
        container.style.transition = '';
        if (dragMoved) {
          savePosition();
          suppressClick = true;
          setTimeout(function () { suppressClick = false; }, 400);
        }
        dragMoved = false;
      }
    }

    handle.addEventListener('mousedown', onPointerDown);
    handle.addEventListener('touchstart', onPointerDown, { passive: true });
    document.addEventListener('mousemove', onPointerMove);
    document.addEventListener('touchmove', onPointerMove, { passive: false });
    document.addEventListener('mouseup', endPointerSession);
    document.addEventListener('touchend', endPointerSession);
    document.addEventListener('touchcancel', endPointerSession);

    handle.addEventListener('click', function (e) {
      if (suppressClick) {
        e.preventDefault();
        e.stopPropagation();
        return;
      }
      if (onTap) {
        e.preventDefault();
        onTap(e);
      }
    });

    handle.addEventListener('contextmenu', function (e) {
      e.preventDefault();
    });

    loadSaved();
    window.addEventListener('resize', function () {
      const style = window.getComputedStyle(container);
      if (!container.getAttribute('data-fab-positioned')) return;
      const side = axis === 'left'
        ? parseInt(style.left, 10)
        : parseInt(style.right, 10);
      const bottom = parseInt(style.bottom, 10);
      applyPosition(
        clamp(side, PADDING, window.innerWidth - FAB_SIZE - PADDING),
        clamp(bottom, PADDING, window.innerHeight - FAB_SIZE - PADDING)
      );
    });
  }

  function boot() {
    initFabDrag({
      container: '#staff-chat-launcher',
      handle: '#staff-chat-launcher',
      storageKey: 'hrms-staff-chat-position',
      axis: 'left',
    });

    initFabDrag({
      container: '#hrms-chatbot',
      handle: '#chatbot-bubble',
      storageKey: 'chatbot-position',
      axis: 'right',
      onTap: function () {
        if (typeof window.toggleHrmsChatbot === 'function') {
          window.toggleHrmsChatbot();
        }
      },
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
