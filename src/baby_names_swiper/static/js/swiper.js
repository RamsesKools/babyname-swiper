// Swipe UX: drag, fly-away animation, keyboard.
//
// Speed model — two cards are always in the DOM:
//   #card        the active card you swipe
//   #card-next   the lookahead, rendered hidden behind it
//
// On a swipe we promote #card-next to #card *immediately* (no network wait),
// animate the old card away on top, and fire the POST in the background.
// The POST records the swipe and returns the next lookahead card, which we
// drop in behind. Result: the next name is on screen the instant you swipe.
//
// Match-celebration overlay lives in match.js as a shared helper.

const SWIPE_THRESHOLD = 110;       // px drag distance to count as a swipe
const FLY_DURATION_MS = 380;       // matches CSS .fly-* transition + a buffer
const GLOW_HOLD_MS = 160;          // brief glow flash before the fly-away starts

// ---- DOM helpers ----

function deck() {
    return document.getElementById("deck");
}

function activeCard() {
    return document.getElementById("card");
}

function nextCard() {
    return document.getElementById("card-next");
}

function cardStack() {
    return document.getElementById("card-stack");
}

function deckConfig() {
    const d = deck();
    if (!d) return null;
    return {
        order: d.dataset.order || "random",
        shuffle: d.dataset.shuffle || "",
    };
}

// ---- swipe core ----

let swiping = false;

// match.js calls into these so the celebration can pause the deck.
window.acquireSwipingLock = () => { swiping = true; };
window.releaseSwipingLock = () => { swiping = false; };

function swipe(direction, { skipGlow = false } = {}) {
    if (swiping) {
        return;
    }
    const active = activeCard();
    const upcoming = nextCard();
    if (!active || !active.dataset.card && active.id !== "card") {
        return;
    }
    if (!upcoming) {
        return;
    }
    swiping = true;

    const cfg = deckConfig();
    const swipedName = active.dataset.name;
    const sourceList = active.dataset.sourceList || "";
    const flyClass = direction === "like" ? "fly-right" : "fly-left";
    const glowClass = direction === "like" ? "glow-like" : "glow-nope";

    // if the card behind is the empty placeholder, this was the last real
    // card: record the swipe and reload the whole deck so the proper
    // end-of-list state is shown (no half-card, no double message)
    const lastCard = upcoming.hasAttribute("data-card-next-empty");

    const launch = () => {
        if (direction === "like") {
            window.burstConfetti(active);
        }
        active.classList.add(flyClass);
        active.removeAttribute("id");
        active.removeAttribute("data-card");
        setTimeout(() => active.remove(), FLY_DURATION_MS);

        if (lastCard) {
            commitLastSwipe(direction, swipedName, sourceList, cfg);
            return;
        }

        promoteNextToActive(upcoming);
        swiping = false;

        if (swipedName && sourceList) {
            commitSwipe(direction, swipedName, sourceList, cfg);
        }
    };

    if (skipGlow) {
        active.classList.remove("glow-like", "glow-nope");
        launch();
        return;
    }
    active.classList.remove("glow-like", "glow-nope");
    active.classList.add(glowClass);
    setTimeout(launch, GLOW_HOLD_MS);
}

function promoteNextToActive(upcoming) {
    upcoming.classList.remove("card-behind", "glow-like", "glow-nope");
    upcoming.style.transform = "";
    upcoming.id = "card";
    upcoming.removeAttribute("data-card-next");
    upcoming.setAttribute("data-card", "");
}

function swipeBody(direction, swipedName, sourceList, cfg) {
    const params = new URLSearchParams({
        name: swipedName,
        direction: direction === "like" ? "1" : "0",
        list: sourceList,
        order: cfg.order,
    });
    if (cfg.shuffle) params.set("shuffle", cfg.shuffle);
    return params;
}

function commitSwipe(direction, swipedName, sourceList, cfg) {
    fetch("/swipe", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: swipeBody(direction, swipedName, sourceList, cfg),
    })
        .then((r) => r.text())
        .then((html) => {
            const stack = cardStack();
            if (!stack) return;
            const existing = nextCard();
            if (existing) {
                existing.remove();
            }
            stack.insertAdjacentHTML("beforeend", html.trim());
            const fresh = nextCard();
            const matchName = fresh && fresh.dataset.matchName;
            if (fresh) {
                fresh.removeAttribute("data-match-name");
            }
            if (matchName) {
                window.showMatchCelebration(matchName);
            }
        })
        .catch(() => {
            /* network hiccup — next swipe will be blocked until reload */
        });
}

// last real card swiped: record it, then rebuild the whole deck so the
// proper end-of-list state renders. The deck swap waits out the fly-away
// animation so the card leaves cleanly.
function commitLastSwipe(direction, swipedName, sourceList, cfg) {
    const animationDone = new Promise((resolve) => {
        setTimeout(resolve, FLY_DURATION_MS);
    });
    let matchName = null;
    fetch("/swipe", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: swipeBody(direction, swipedName, sourceList, cfg),
    })
        .then((r) => r.text())
        .then((postHtml) => {
            const frag = new DOMParser().parseFromString(postHtml, "text/html");
            const el = frag.querySelector("[data-match-name]");
            matchName = el ? el.getAttribute("data-match-name") : null;
            let url = `/swipe?order=${encodeURIComponent(cfg.order)}`;
            if (cfg.shuffle) {
                url += `&shuffle=${encodeURIComponent(cfg.shuffle)}`;
            }
            return fetch(url);
        })
        .then((r) => r.text())
        .then((html) => Promise.all([html, animationDone]))
        .then(([html]) => {
            const doc = new DOMParser().parseFromString(html, "text/html");
            const freshDeck = doc.getElementById("deck");
            const currentDeck = deck();
            if (freshDeck && currentDeck) {
                currentDeck.replaceWith(freshDeck);
            }
            swiping = false;
            if (matchName) {
                window.showMatchCelebration(matchName);
            }
        })
        .catch(() => {
            swiping = false;
        });
}

function undo() {
    const cfg = deckConfig();
    if (!cfg) return;
    const body = new URLSearchParams({ order: cfg.order });
    if (cfg.shuffle) body.set("shuffle", cfg.shuffle);
    fetch("/swipe/undo", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body,
    })
        .then((r) => r.text())
        .then((html) => {
            const d = deck();
            if (d) {
                d.outerHTML = html.trim();
                swiping = false;
            }
        })
        .catch(() => { /* ignore */ });
}

// ---- keyboard shortcuts ----

document.addEventListener("keydown", (event) => {
    const t = event.target;
    if (t && (t.tagName === "INPUT" || t.tagName === "SELECT" || t.tagName === "TEXTAREA")) {
        return;
    }
    if (event.key === "ArrowRight") {
        event.preventDefault();
        swipe("like");
    } else if (event.key === "ArrowLeft") {
        event.preventDefault();
        swipe("nope");
    } else if (event.key === "Backspace") {
        event.preventDefault();
        undo();
    }
});

// ---- swipe controls (list / order / state) submit the form on change ----

(function () {
    const form = document.getElementById("swipe-controls");
    if (!form) return;
    form.addEventListener("change", () => {
        form.submit();
    });
})();

// ---- button clicks ----

document.body.addEventListener("click", (event) => {
    const btn = event.target.closest("[data-action]");
    if (!btn) {
        return;
    }
    const action = btn.dataset.action;
    if (action === "like" || action === "nope") {
        swipe(action);
    } else if (action === "undo") {
        undo();
    }
});

// ---- drag / pointer gestures ----

let drag = null;

function onPointerDown(event) {
    const card = event.target.closest("#card");
    if (!card || swiping) {
        return;
    }
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
    const dy = (event.clientY - drag.startY) * 0.2;
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
        swipe("like", { skipGlow: true });
        return;
    }
    if (dx < -SWIPE_THRESHOLD) {
        drag = null;
        swipe("nope", { skipGlow: true });
        return;
    }
    card.style.transform = "";
    card.classList.remove("glow-like", "glow-nope");
    drag = null;
}

document.addEventListener("pointerdown", onPointerDown);
document.addEventListener("pointermove", onPointerMove);
document.addEventListener("pointerup", onPointerUp);
document.addEventListener("pointercancel", onPointerUp);
