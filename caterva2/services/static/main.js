function activate(selector, trigger) {
    const url = new URL(document.URL);
    for (el of document.querySelectorAll(selector)) {
        if (trigger === undefined) {
            const href = new URL(el.href)
            if (url.pathname.startsWith(href.pathname)) {
                el.classList.add("active");
            }
            else {
                el.classList.remove("active");
            }
        }
        else {
            if (trigger == el) {
                el.classList.add("active");
            }
            else {
                el.classList.remove("active");
            }
        }
    }
}

function clearContent(elementID) {
    document.getElementById(elementID).innerHTML = "";
}
