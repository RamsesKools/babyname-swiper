# Design Decisions

A log of UX and product decisions for the app. Grouped by area, then by feature. Keep entries short and behaviour-focused.

## Users & sessions

### Who can use the app

- Only two users exist (Ramses, Chiara), hardcoded -- no registration.
- The user picker page is the gate: every route redirects to `/who` if there is no valid signed cookie.
- "switch user" logs out (clears the cookie) and sends you back to the picker.

### Login persistence

- A signed cookie remembers the user for one year.
- The cookie is `httpOnly` and `SameSite=Lax`.
- A tampered or stale cookie is treated as "logged out" rather than an error, so users land on the picker silently.
- Rotating `COOKIE_SECRET` invalidates every existing cookie and forces everyone back to the picker.

## Navigation

### Header layout

- The header uses the shared `.nav` pattern from the `webserver-homepage-config` project (CSS lives in `static/css/my-design.css`).
- On narrow screens the nav links collapse behind a hamburger; clicking it toggles `.open` on `#nav` to reveal a stacked menu.
- Behaviour (hamburger, dropdown clicks) lives in `static/js/nav.js`; markup stays in the Jinja template because the header has server-side conditionals.
- Only one dropdown can be open at a time; clicking outside closes any click-driven dropdown.
- Dropdowns open on hover on desktop and on click on mobile; the "Add name(s)" dropdown is forced to click-on-desktop too so it doesn't snap shut when the cursor drifts.

### Active user indicator

- The logged-in user is shown as a header dropdown labelled "Active user: {name}".
- "switch user" lives inside that dropdown's panel rather than as a top-level link, so the header stays compact.

### Reusable menu-button helper

- `nav.js` exposes `window.addMenuButton(label, items)` for adding a new link-list dropdown to the header in one call.
- Currently unused; kept so future overflow / settings menus can be added without re-deriving the markup and wiring.

## Adding names

### Adding names to lists

- A single "Add name(s)" entry in the nav opens a dropdown with two choices: upload a file, or add a single name.
- The dropdown opens on click and stays open until the user clicks the trigger again or clicks outside (no hover-to-open).
- "Add single name" reveals an inline input within the same dropdown panel; clicking it a second time closes the whole dropdown.
- After submitting a name, the dropdown reopens automatically with the input focused, so several names can be queued in a row.
- After submitting, the user lands back on the page they submitted from (e.g. overview stays on overview), not always on the swipe page.

### Handling duplicates and prior swipes

- Typing a name that already exists in the list does not add it again, but does record a like for the current user.
- Typing a name the user previously disliked flips that swipe to a like.
- Name matching is case-insensitive; the like is tied to the canonical casing stored in the list.
- Invalid input (empty or too long) is a silent no-op.

### Uploading a list

- A list is just a CSV file; the filename (sanitised) becomes the list slug, prefixed `upload_`.
- Uploads cap at 1 MiB, 5000 names per list, 50 characters per name; excess is silently truncated rather than rejected.
- Files must be UTF-8; anything else is rejected with a clear error.
- Names in the upload are de-duplicated case-insensitively and stored alphabetically.
- An upload that ends up empty after cleaning fails with an error rather than creating an empty list.
- After a successful upload the user lands on the swipe page for the new list.

## Swiping

### Card stack

- Two cards are always in the DOM: the active card and a "lookahead" peek of the next one (slightly smaller and dimmed).
- Swiping promotes the lookahead instantly; the network request runs in the background and never blocks the UI.
- A swipe registers when the drag passes a fixed threshold (about 110px); shorter drags spring back.
- During a swipe-out animation, further swipes are ignored until it completes.

### Keyboard

- Arrow Right = like, Arrow Left = nope, Backspace = undo.
- Keyboard shortcuts are disabled while typing in an input/select/textarea so they don't fight the add-name field.

### Undo

- Undo only ever undoes the most recent swipe for the active user + list (LIFO).
- There is no multi-step undo history.

### End of deck

- The lookahead slot shows a placeholder ("That's the last one in this list") when the deck is exhausted.
- Swiping the last real card rebuilds the deck from scratch rather than appending more lookahead.

### Mode and list switching

- Changing list, mode, or the reswipe checkbox triggers a full page reload (no in-place swap).
- An unknown list slug falls back silently to the first available list rather than 404'ing.

## Deck composition

### Modes

- "random" weights the shuffle: names the partner liked are 5x more likely to show, names the partner disliked 5x less likely.
- "alpha" shows names case-folded A-Z.
- "partner likes only" filters the deck to names the partner liked and then sorts alphabetically.
- The random shuffle is seeded per (user, list, mode) so the order is stable across reloads in the same session.

### Reswipe

- "reswipe disliked" re-includes names the active user previously disliked, so dislikes can be revisited.
- Likes are never replayed; once liked, a name stays out of the deck.

## Matching

### What counts as a match

- A match is when both users have a like recorded for the same name in the same list.
- A new-match celebration only fires on the swipe that *creates* the match (i.e. this user likes a name the partner had already liked).

### Match celebration

- A full-screen overlay shows the matched card and "it's a match!" text.
- Confetti fires twice (immediately and again at ~600ms) for a sustained burst.
- The overlay holds for 10 seconds and can be dismissed early by clicking it.
- Swiping is blocked while the celebration is on screen.

### Animation feedback

- Confetti only fires on like, never on dislike.
- The card glows green for ~160ms before flying right on like, red before flying left on nope.
- Drag tilts the card up to ±25 degrees proportional to drag distance.
- "LIKE" and "NOPE" stamps fade in as the drag approaches the threshold and reach full opacity at threshold.

## Overview page

### Layout

- Four stats are pinned at the top: total names, swiped, remaining, matches.
- Names are grouped into collapsible sections: matches, partner likes, your likes, your dislikes.
- "Your likes" is the only section open by default; the rest start collapsed.
- Disliked names are rendered with a strikethrough so the visual state matches the meaning.

### Quick actions

- The "partner likes" section has a shortcut link straight to swipe mode "partner likes only".
- Each section header has a destructive action (reset that section) styled in red and gated by a confirmation prompt.

### Removing vs deleting names

- "Remove" (the × button) erases only the current user's swipe for that name; the name stays in the list for the other user.
- "Delete" (only visible on manually-added names) removes the name from the list entirely and wipes both users' swipes for it.
- Names from the base CSV (`boys`, `girls`, uploaded lists' originals) cannot be deleted individually -- only manual additions can.
- Delete is gated by a confirmation prompt; remove is not.

### Reset list

- "Reset list" wipes both likes and dislikes for the current user and is confirmation-gated as irreversible.
- It does not touch the other user's swipes or remove names from the list.

## Lists & storage

### Built-in vs uploaded lists

- "boys" and "girls" ship with the app and are always available.
- Uploaded lists are surfaced with the `upload_` prefix in the slug but a clean label in the picker.
- The list dropdown shows built-ins first, then uploads, each alphabetised.

### Manually added names

- Manually added names live in a per-list `manual_<slug>.csv` file alongside the base CSV.
- Loading a list merges base names and manual names; duplicates between them are case-folded and collapsed.

### Persistence

- Swipes are stored in SQLite, keyed by (user, list, name), so re-swiping the same name updates rather than duplicates.
- The deck is rebuilt from the swipe history on every server restart -- no in-memory state needs to survive.

## Errors & resilience

- Swipe POSTs are fire-and-forget; a failed network call does not block or roll back the UI.
- An invalid or missing list slug falls back to the first available list rather than erroring.
- If no name lists exist at all, the swipe page returns 503 with a clear message rather than rendering an empty UI.
