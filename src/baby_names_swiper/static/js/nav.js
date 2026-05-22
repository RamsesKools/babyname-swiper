/* Header navigation behaviour.

   The markup itself is rendered server-side by templates/_header.html (it has
   Jinja conditionals, so it can't be built in JS like the homepage project's
   nav). This script only wires up the *interactive* bits:

     1. The hamburger button -- on narrow screens the nav links are hidden by
        CSS and only shown when #nav has the .open class. Clicking the
        hamburger toggles that class.

     2. Dropdowns (.nav-dropdown) -- on desktop these open on :hover via CSS.
        On mobile there is no hover, so we toggle a .dropdown-open class on
        click instead. */

(function () {
    var nav = document.getElementById('nav');
    if (!nav) return;

    /* nav.js can run before the rest of the page, so mark the element ready. */
    nav.classList.add('nav');

    var hamburger = nav.querySelector('.nav-hamburger');

    /* --- Hamburger toggle --- */
    if (hamburger) {
        hamburger.addEventListener('click', function () {
            nav.classList.toggle('open');
        });
    }

    /* Close every open dropdown except the one passed in -- keeps only one
       panel open at a time on mobile. */
    function closeOtherDropdowns(except) {
        var open = nav.querySelectorAll('.nav-dropdown.dropdown-open');
        for (var i = 0; i < open.length; i++) {
            if (open[i] !== except) open[i].classList.remove('dropdown-open');
        }
    }

    /* True when the hamburger is visible, i.e. we're on a narrow/mobile
       layout. Desktop relies on CSS :hover and needs no JS toggling. */
    function isMobileLayout() {
        return hamburger && window.getComputedStyle(hamburger).display !== 'none';
    }

    /* --- Dropdown click handling (mobile) --- */
    var dropdowns = nav.querySelectorAll('.nav-dropdown');
    for (var d = 0; d < dropdowns.length; d++) {
        (function (dropdown) {
            /* The trigger is either a real link (<a>) or a plain label
               (<span class="nav-dropdown-label">). */
            var trigger = dropdown.querySelector(':scope > a')
                       || dropdown.querySelector(':scope > .nav-dropdown-label');
            if (!trigger) return;
            var isLink = trigger.tagName === 'A';

            trigger.addEventListener('click', function (e) {
                /* Desktop: a link just navigates, hover handles the panel. */
                if (!isMobileLayout()) return;

                if (isLink) {
                    /* Mobile link: first tap expands the panel, second tap
                       follows the link. */
                    if (!dropdown.classList.contains('dropdown-open')) {
                        e.preventDefault();
                        closeOtherDropdowns(dropdown);
                        dropdown.classList.add('dropdown-open');
                    }
                    return;
                }

                /* Mobile label (not a link): just toggle the panel. */
                e.preventDefault();
                closeOtherDropdowns(dropdown);
                dropdown.classList.toggle('dropdown-open');
            });
        })(dropdowns[d]);
    }

    /* ------------------------------------------------------------------
       Reusable "menu button" helper -- a button in the header that, when
       clicked, reveals a list of links (the same dropdown pattern used by
       "+ name", but for plain navigation links).

       Nothing calls this yet. It's here so that when we *do* need an extra
       menu (e.g. a "more" overflow menu, or grouped settings links), we can
       add it in one line instead of re-deriving the markup + wiring.

       Usage example (run after this script, e.g. from a page template):

           addMenuButton('more', [
               { label: 'about',    href: '/about' },
               { label: 'settings', href: '/settings' },
           ]);

       This appends:

           <li class="nav-dropdown">
               <span class="nav-dropdown-label">more</span>
               <ul class="nav-dropdown-menu">
                   <li><a href="/about">about</a></li>
                   <li><a href="/settings">settings</a></li>
               </ul>
           </li>

       which is automatically styled by my-design.css and gets the same
       hover (desktop) / click (mobile) behaviour as the other dropdowns.
       ------------------------------------------------------------------ */
    function addMenuButton(label, items) {
        var list = nav.querySelector('.nav-links');
        if (!list) return;

        var dropdown = document.createElement('li');
        dropdown.className = 'nav-dropdown';

        /* Plain (non-link) trigger -- it only opens the menu. */
        var triggerLabel = document.createElement('span');
        triggerLabel.className = 'nav-dropdown-label';
        triggerLabel.textContent = label;
        dropdown.appendChild(triggerLabel);

        /* The menu panel: one <li><a> per item. */
        var menu = document.createElement('ul');
        menu.className = 'nav-dropdown-menu';
        for (var i = 0; i < items.length; i++) {
            var li = document.createElement('li');
            var a = document.createElement('a');
            a.href = items[i].href;
            a.textContent = items[i].label;
            /* Highlight the link for the page we're currently on. */
            if (location.pathname === items[i].href) a.classList.add('active');
            li.appendChild(a);
            menu.appendChild(li);
        }
        dropdown.appendChild(menu);
        list.appendChild(dropdown);

        /* Wire up the mobile click-to-open behaviour for this new dropdown. */
        triggerLabel.addEventListener('click', function (e) {
            if (!isMobileLayout()) return;
            e.preventDefault();
            closeOtherDropdowns(dropdown);
            dropdown.classList.toggle('dropdown-open');
        });

        return dropdown;
    }

    /* Expose the helper so page-specific scripts can call it if needed. */
    window.addMenuButton = addMenuButton;
})();
