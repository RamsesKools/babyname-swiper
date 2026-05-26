// Swipe UX: drag, fly-away animation, keyboard, confetti.
//
// Speed model — two cards are always in the DOM:
//   #card        the active card you swipe
//   #card-next   the lookahead, rendered hidden behind it
//
// On a swipe we promote #card-next to #card *immediately* (no network wait),
// animate the old card away on top, and fire the POST in the background.
// The POST records the swipe and returns the next lookahead card, which we
// drop in behind. Result: the next name is on screen the instant you swipe.

const SWIPE_THRESHOLD = 110;       // px drag distance to count as a swipe
const FLY_DURATION_MS = 380;       // matches CSS .fly-* transition + a buffer
const GLOW_HOLD_MS = 160;          // brief glow flash before the fly-away starts
const MATCH_HOLD_MS = 10000;       // how long the new-match celebration stays up

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
        list: d.dataset.list,
        order: d.dataset.order || "random",
        reswipe: d.dataset.reswipe || "0",
        shuffle: d.dataset.shuffle || "",
    };
}

// ---- swipe core ----

let swiping = false;

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
        // lookahead hasn't arrived yet (very fast double-swipe) — ignore
        return;
    }
    swiping = true;

    const cfg = deckConfig();
    const swipedName = active.dataset.name;
    const flyClass = direction === "like" ? "fly-right" : "fly-left";
    const glowClass = direction === "like" ? "glow-like" : "glow-nope";

    // if the card behind is the empty placeholder, this was the last real
    // card: record the swipe and reload the whole deck so the proper
    // end-of-list state is shown (no half-card, no double message)
    const lastCard = upcoming.hasAttribute("data-card-next-empty");

    const launch = () => {
        // 1. confetti from the still-centered card
        if (direction === "like") {
            burstConfetti(active);
        }
        // 2. fly the old card away
        active.classList.add(flyClass);
        active.removeAttribute("id");
        active.removeAttribute("data-card");
        setTimeout(() => active.remove(), FLY_DURATION_MS);

        if (lastCard) {
            // no real card left — record, then rebuild the deck end state
            commitLastSwipe(direction, swipedName, cfg);
            return;
        }

        // 3. promote the lookahead card to active — instant, already in DOM
        promoteNextToActive(upcoming);

        // 4. allow the next swipe right away
        swiping = false;

        // 5. record + fetch the new lookahead in the background
        if (swipedName) {
            commitSwipe(direction, swipedName, cfg);
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

function swipeBody(direction, swipedName, cfg) {
    const params = new URLSearchParams({
        name: swipedName,
        direction: direction === "like" ? "1" : "0",
        list: cfg.list,
        order: cfg.order,
        reswipe: cfg.reswipe,
    });
    if (cfg.shuffle) params.set("shuffle", cfg.shuffle);
    return params;
}

function commitSwipe(direction, swipedName, cfg) {
    fetch("/swipe", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: swipeBody(direction, swipedName, cfg),
    })
        .then((r) => r.text())
        .then((html) => {
            const stack = cardStack();
            if (!stack) return;
            // drop the fresh lookahead behind the active card
            const existing = nextCard();
            if (existing) {
                existing.remove();
            }
            stack.insertAdjacentHTML("beforeend", html.trim());
            // the freshly-inserted lookahead carries the match flag for the
            // name we just swiped; pull it off and run the celebration
            const fresh = nextCard();
            const matchName = fresh && fresh.dataset.matchName;
            if (fresh) {
                fresh.removeAttribute("data-match-name");
            }
            if (matchName) {
                showMatchCelebration(matchName);
            }
        })
        .catch(() => {
            /* network hiccup — next swipe will be blocked until reload */
        });
}

// last real card swiped: record it, then rebuild the whole deck so the
// proper end-of-list state renders (avoids a stranded empty placeholder).
// The deck swap waits out the fly-away animation so the card leaves cleanly.
function commitLastSwipe(direction, swipedName, cfg) {
    const animationDone = new Promise((resolve) => {
        setTimeout(resolve, FLY_DURATION_MS);
    });
    let matchName = null;
    fetch("/swipe", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: swipeBody(direction, swipedName, cfg),
    })
        .then((r) => r.text())
        .then((postHtml) => {
            // the POST response is the empty lookahead fragment; it carries
            // the match flag for the name we just swiped
            const frag = new DOMParser().parseFromString(postHtml, "text/html");
            const el = frag.querySelector("[data-match-name]");
            matchName = el ? el.getAttribute("data-match-name") : null;
            let url = `/swipe?list=${encodeURIComponent(cfg.list)}`
                + `&order=${encodeURIComponent(cfg.order)}`
                + `&reswipe=${encodeURIComponent(cfg.reswipe)}`;
            if (cfg.shuffle) {
                url += `&shuffle=${encodeURIComponent(cfg.shuffle)}`;
            }
            return fetch(url);
        })
        .then((r) => r.text())
        .then((html) => Promise.all([html, animationDone]))
        .then(([html]) => {
            // pull just the #deck fragment out of the full page response
            const doc = new DOMParser().parseFromString(html, "text/html");
            const freshDeck = doc.getElementById("deck");
            const currentDeck = deck();
            if (freshDeck && currentDeck) {
                currentDeck.replaceWith(freshDeck);
            }
            swiping = false;
            if (matchName) {
                showMatchCelebration(matchName);
            }
        })
        .catch(() => {
            swiping = false;
        });
}

function undo() {
    const cfg = deckConfig();
    if (!cfg) return;
    const body = new URLSearchParams({
        list: cfg.list,
        order: cfg.order,
        reswipe: cfg.reswipe,
    });
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

// ---- swipe controls (list / order / reswipe) reload the page on change ----

document.addEventListener("change", (event) => {
    const target = event.target;
    if (!target || !target.dataset || !target.dataset.control) {
        return;
    }
    const key = target.dataset.control;
    const url = new URL(window.location.href);
    if (target.type === "checkbox") {
        if (target.checked) {
            url.searchParams.set(key, "1");
        } else {
            url.searchParams.delete(key);
        }
    } else {
        url.searchParams.set(key, target.value);
    }
    // The shuffle token already in the URL is preserved across control changes
    // by re-using the existing URL above. Nothing extra to do for list/reswipe.
    window.location.href = url.toString();
});

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

// ---- reshuffle button: drop the shuffle token from the URL and reload so
// the server mints a fresh one. ----

const reshuffleBtn = document.getElementById("swipe-reshuffle");
if (reshuffleBtn) {
    reshuffleBtn.addEventListener("click", () => {
        const url = new URL(window.location.href);
        url.searchParams.delete("shuffle");
        window.location.href = url.toString();
    });
}

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
        const dy = Math.sin(angle) * distance + 200;
        piece.style.setProperty("--dx", `${dx}px`);
        piece.style.setProperty("--dy", `${dy}px`);
        piece.style.setProperty("--rot", `${(Math.random() - 0.5) * 720}deg`);
        document.body.appendChild(piece);
        setTimeout(() => piece.remove(), 950);
    }
}

// ---- new-match celebration ----
//
// When a like creates a match, a celebration card pops in over the deck:
// green glow, a "YOU BOTH LIKED" stamp, and a shout-out banner. Swiping is
// frozen for MATCH_HOLD_MS; clicking the card dismisses it early.

let matchCelebrationActive = false;

function dismissMatchCelebration(overlay) {
    if (!overlay || !overlay.isConnected) {
        return;
    }
    overlay.classList.add("match-overlay-out");
    setTimeout(() => overlay.remove(), 250);
    matchCelebrationActive = false;
    swiping = false;
}

function showMatchCelebration(name) {
    if (matchCelebrationActive) {
        return;
    }
    matchCelebrationActive = true;
    // block swiping for the duration of the celebration
    swiping = true;

    const overlay = document.createElement("div");
    overlay.className = "match-overlay";
    overlay.innerHTML = `
        <div class="match-shout">It's a match!</div>
        <div class="card match-card glow-like">
            <div class="stamp stamp-match">you both liked</div>
            <div class="name">${name}</div>
            <div class="meta">tap to continue</div>
        </div>
        <div class="match-hint">you both swiped right</div>
    `;
    document.body.appendChild(overlay);

    const card = overlay.querySelector(".match-card");
    burstConfetti(card);
    // a second burst part-way through keeps the celebration lively
    setTimeout(() => {
        if (overlay.isConnected) burstConfetti(card);
    }, 600);

    overlay.addEventListener("click", () => dismissMatchCelebration(overlay));
    setTimeout(() => dismissMatchCelebration(overlay), MATCH_HOLD_MS);
}
