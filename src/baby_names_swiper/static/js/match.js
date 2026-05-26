// Shared match-celebration overlay. Used by /swipe and /lists.
//
// Exposes two globals (no module system in this app):
//   window.showMatchCelebration(name)  -- overlay + confetti + 10s hold
//   window.burstConfetti(originEl)     -- confetti burst from an element
//
// `/swipe` also needs to suspend its swiping flag during the celebration; it
// reads/writes `window.swipingLock` so this file stays independent.

const CONFETTI_COLORS = ["#3E77DC", "#70AE6E", "#F29559", "#F55D3E", "#A8C3F0"];
const MATCH_HOLD_MS = 10000;

let matchCelebrationActive = false;

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

function dismissMatchCelebration(overlay) {
    if (!overlay || !overlay.isConnected) {
        return;
    }
    overlay.classList.add("match-overlay-out");
    setTimeout(() => overlay.remove(), 250);
    matchCelebrationActive = false;
    // Release the swipe-lock if /swipe set one.
    if (typeof window.releaseSwipingLock === "function") {
        window.releaseSwipingLock();
    }
}

function showMatchCelebration(name) {
    if (matchCelebrationActive) {
        return;
    }
    matchCelebrationActive = true;
    // /swipe sets this to block swipes for the duration of the celebration.
    if (typeof window.acquireSwipingLock === "function") {
        window.acquireSwipingLock();
    }

    const overlay = document.createElement("div");
    overlay.className = "match-overlay";
    overlay.innerHTML = `
        <div class="match-shout">It's a match!</div>
        <div class="card match-card glow-like">
            <div class="stamp stamp-match">you both liked</div>
            <div class="name"></div>
            <div class="meta">tap to continue</div>
        </div>
        <div class="match-hint">you both swiped right</div>
    `;
    // Set the name via textContent so it can't introduce HTML.
    overlay.querySelector(".name").textContent = name;
    document.body.appendChild(overlay);

    const card = overlay.querySelector(".match-card");
    burstConfetti(card);
    setTimeout(() => {
        if (overlay.isConnected) burstConfetti(card);
    }, 600);

    overlay.addEventListener("click", () => dismissMatchCelebration(overlay));
    setTimeout(() => dismissMatchCelebration(overlay), MATCH_HOLD_MS);
}

window.burstConfetti = burstConfetti;
window.showMatchCelebration = showMatchCelebration;
