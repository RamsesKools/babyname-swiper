/* /lists page wiring.
   The controls form (#lists-controls) is a plain GET form. We just submit
   it whenever any input changes, matching the "change list/order triggers
   a full reload" pattern used on /swipe. */

(function () {
    var form = document.getElementById('lists-controls');
    if (!form) return;

    form.addEventListener('change', function () {
        form.submit();
    });

    /* Reshuffle: clear the shuffle token from the URL and reload so the
       server mints a fresh one. Single source of truth for the token format. */
    var reshuffle = document.getElementById('lists-reshuffle');
    var shuffleInput = document.getElementById('lists-shuffle');
    if (reshuffle && shuffleInput) {
        reshuffle.addEventListener('click', function () {
            shuffleInput.value = '';
            form.submit();
        });
    }
})();
