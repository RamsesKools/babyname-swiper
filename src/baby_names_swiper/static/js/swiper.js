// Swipe UX: drag, button fly-away, keyboard, confetti on like.
// All animations stay client-side; HTMX still does the swap.
//
// Flow:
//   1. user triggers like/nope (button, key, or drag past threshold)
//   2. card gets a fly-left/fly-right class
//   3. on transitionend we programmatically POST to /swipe via HTMX
//   4. server returns the next _card.html partial and HTMX swaps #card

const SWIPE_THRESHOLD = 110;       // px drag distance to count as a swipe
const FLY_DURATION_MS = 380;       // matches CSS .fly-* transition + a buffer
const GLOW_HOLD_MS = 160;          // brief glow flash before the fly-away starts

// ---- helpers ----

function findCard() {
    return document.querySelector("[data-card]");
}

function findButton(action) {
    return document.querySelector(`[data-action='${action}']`);
}

function commit(action) {
    // Use htmx.ajax so we don't fight with our own click handler.
    const btn = findButton(action);
    if (!btn || !window.htmx) {
        if (btn) btn.click();
        return;
    }
    const url = btn.getAttribute("hx-post") || btn.getAttribute("hx-get");
    const method = btn.getAttribute("hx-post") ? "POST" : "GET";
    let values = {};
    try {
        values = JSON.parse(btn.getAttribute("hx-vals") || "{}");
    } catch (_) {
        values = {};
    }
    window.htmx.ajax(method, url, {
        target: btn.getAttribute("hx-target") || "#card",
        swap: btn.getAttribute("hx-swap") || "outerHTML",
        values,
    });
}

function flyAndCommit(direction, { skipGlow = false } = {}) {
    const card = findCard();
    if (!card || card.dataset.flying === "1") {
        return;
    }
    card.dataset.flying = "1";

    const flyClass = direction === "like" ? "fly-right" : "fly-left";
    const glowClass = direction === "like" ? "glow-like" : "glow-nope";

    const launch = () => {
        card.classList.add(flyClass);
        if (direction === "like") {
            burstConfetti(card);
        }
        setTimeout(() => commit(direction), FLY_DURATION_MS);
    };

    if (skipGlow) {
        card.classList.remove("glow-like", "glow-nope");
        launch();
        return;
    }

    // make sure only the matching glow is on, then hold briefly before launch
    card.classList.remove("glow-like", "glow-nope");
    card.classList.add(glowClass);
    setTimeout(launch, GLOW_HOLD_MS);
}

// ---- keyboard shortcuts ----

document.addEventListener("keydown", (event) => {
    const t = event.target;
    if (t && (t.tagName === "INPUT" || t.tagName === "SELECT" || t.tagName === "TEXTAREA")) {
        return;
    }
    if (event.key === "ArrowRight") {
        event.preventDefault();
        flyAndCommit("like");
    } else if (event.key === "ArrowLeft") {
        event.preventDefault();
        flyAndCommit("nope");
    } else if (event.key === "Backspace") {
        event.preventDefault();
        triggerHtmx("undo");
    }
});

// ---- list-picker dropdown ----

document.addEventListener("change", (event) => {
    const target = event.target;
    if (target && target.id === "list-picker") {
        const url = new URL(window.location.href);
        url.searchParams.set("list", target.value);
        window.location.href = url.toString();
    }
});

// ---- button click -> fly-away animation (intercepts HTMX) ----

document.body.addEventListener("click", (event) => {
    const btn = event.target.closest("[data-action]");
    if (!btn) {
        return;
    }
    const action = btn.dataset.action;
    if (action === "like" || action === "nope") {
        event.preventDefault();
        event.stopPropagation();
        flyAndCommit(action);
    }
    // "undo" falls through and lets HTMX handle it directly
}, true);

// ---- drag / pointer gestures ----

let drag = null;

function onPointerDown(event) {
    const card = event.target.closest("[data-card]");
    if (!card || card.dataset.flying === "1") {
        return;
    }
    // ignore clicks on buttons inside the card area (none currently, but safe)
    if (event.target.closest("button")) {
        return;
    }
    drag = {
        card,
        startX: event.clientX,
        startY: event.clientY,
        dx: 0,
        pointerId: event.pointerId,
    };
    card.setPointerCapture(event.pointerId);
    card.classList.add("dragging");
}

function onPointerMove(event) {
    if (!drag || event.pointerId !== drag.pointerId) {
        return;
    }
    drag.dx = event.clientX - drag.startX;
    const dy = (event.clientY - drag.startY) * 0.2;     // small vertical follow
    const rot = drag.dx / 20;
    drag.card.style.transform = `translate(${drag.dx}px, ${dy}px) rotate(${rot}deg)`;

    const intensity = Math.min(Math.abs(drag.dx) / SWIPE_THRESHOLD, 1);
    const likeStamp = drag.card.querySelector(".stamp-like");
    const nopeStamp = drag.card.querySelector(".stamp-nope");
    if (drag.dx > 0) {
        if (likeStamp) likeStamp.style.opacity = intensity;
        if (nopeStamp) nopeStamp.style.opacity = 0;
        drag.card.classList.add("glow-like");
        drag.card.classList.remove("glow-nope");
    } else if (drag.dx < 0) {
        if (nopeStamp) nopeStamp.style.opacity = intensity;
        if (likeStamp) likeStamp.style.opacity = 0;
        drag.card.classList.add("glow-nope");
        drag.card.classList.remove("glow-like");
    } else {
        drag.card.classList.remove("glow-like", "glow-nope");
    }
}

function onPointerUp(event) {
    if (!drag || event.pointerId !== drag.pointerId) {
        return;
    }
    const { card, dx } = drag;
    card.classList.remove("dragging");
    try { card.releasePointerCapture(drag.pointerId); } catch (_) { /* ignore */ }
    const likeStamp = card.querySelector(".stamp-like");
    const nopeStamp = card.querySelector(".stamp-nope");
    if (likeStamp) likeStamp.style.opacity = 0;
    if (nopeStamp) nopeStamp.style.opacity = 0;

    if (dx > SWIPE_THRESHOLD) {
        drag = null;
        flyAndCommit("like", { skipGlow: true });
        return;
    }
    if (dx < -SWIPE_THRESHOLD) {
        drag = null;
        flyAndCommit("nope", { skipGlow: true });
        return;
    }
    // snap back
    card.style.transform = "";
    card.classList.remove("glow-like", "glow-nope");
    drag = null;
}

document.addEventListener("pointerdown", onPointerDown);
document.addEventListener("pointermove", onPointerMove);
document.addEventListener("pointerup", onPointerUp);
document.addEventListener("pointercancel", onPointerUp);

// ---- confetti ----

const CONFETTI_COLORS = ["#3E77DC", "#70AE6E", "#F29559", "#F55D3E", "#A8C3F0"];

function burstConfetti(originEl) {
    const rect = originEl.getBoundingClientRect();
    const ox = rect.left + rect.width / 2;
    const oy = rect.top + rect.height / 2;
    const count = 28;
    for (let i = 0; i < count; i++) {
        const piece = document.createElement("div");
        piece.className = "confetti-piece";
        piece.style.left = `${ox}px`;
        piece.style.top = `${oy}px`;
        piece.style.background = CONFETTI_COLORS[i % CONFETTI_COLORS.length];
        const angle = (Math.PI * 2 * i) / count + Math.random() * 0.4;
        const distance = 140 + Math.random() * 160;
        const dx = Math.cos(angle) * distance;
        const dy = Math.sin(angle) * distance + 200; // bias downward = gravity
        piece.style.setProperty("--dx", `${dx}px`);
        piece.style.setProperty("--dy", `${dy}px`);
        piece.style.setProperty("--rot", `${(Math.random() - 0.5) * 720}deg`);
        document.body.appendChild(piece);
        setTimeout(() => piece.remove(), 950);
    }
}
