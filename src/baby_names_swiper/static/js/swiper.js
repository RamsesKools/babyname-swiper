// Keyboard shortcuts: ArrowLeft = nope, ArrowRight = like, Backspace = undo.
// Triggers the existing HTMX-attributed buttons inside #card so all state
// lives in the server-rendered partial.
document.addEventListener("keydown", (event) => {
    if (event.target && (event.target.tagName === "INPUT" || event.target.tagName === "SELECT" || event.target.tagName === "TEXTAREA")) {
        return;
    }
    let selector = null;
    if (event.key === "ArrowRight") {
        selector = "[data-action='like']";
    } else if (event.key === "ArrowLeft") {
        selector = "[data-action='nope']";
    } else if (event.key === "Backspace") {
        selector = "[data-action='undo']";
    }
    if (!selector) {
        return;
    }
    const btn = document.querySelector(selector);
    if (btn) {
        event.preventDefault();
        btn.click();
    }
});

// List-picker: change the URL to ?list=<slug> on selection so refresh keeps the choice.
document.addEventListener("change", (event) => {
    const target = event.target;
    if (target && target.id === "list-picker") {
        const url = new URL(window.location.href);
        url.searchParams.set("list", target.value);
        window.location.href = url.toString();
    }
});
