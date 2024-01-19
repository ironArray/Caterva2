function activate(selector) {
    const url = new URL(document.URL);
    for (el of document.querySelectorAll(selector)) {
        const href = new URL(el.href)
        if (url.pathname.startsWith(href.pathname)) {
            el.classList.add("active");
        }
        else {
            el.classList.remove("active");
        }
    }
}

function clearcontent(elementID) {
    document.getElementById(elementID).innerHTML = "";
}
