/* /lists page wiring.
   The controls form (#lists-controls) is a plain GET form. We just submit
   it whenever any input changes, matching the "change list/mode triggers
   a full reload" pattern used on /swipe. */

(function () {
    var form = document.getElementById('lists-controls');
    if (!form) return;

    form.addEventListener('change', function () {
        form.submit();
    });
})();
