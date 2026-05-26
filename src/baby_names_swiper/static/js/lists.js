/* /lists page wiring.
   The controls form (#lists-controls) is a plain GET form. We just submit
   it whenever any input changes, matching the "change list/order triggers
   a full reload" pattern used on /swipe.

   Match celebration: when a per-row like creates a match, the server adds
   `HX-Trigger: {"matchCreated": {"name": "..."}}` to the row response. HTMX
   dispatches a "matchCreated" event on the target row; we listen on the
   document and call the shared celebration helper. */

(function () {
    var form = document.getElementById('lists-controls');
    if (form) {
        form.addEventListener('change', function () {
            form.submit();
        });
    }

    document.body.addEventListener('matchCreated', function (event) {
        var detail = event.detail || {};
        if (detail.name && typeof window.showMatchCelebration === 'function') {
            window.showMatchCelebration(detail.name);
        }
    });
})();
