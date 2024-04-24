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

async function submitForm(form, errorElementID="error") {
    const error = document.getElementById(errorElementID);
    error.replaceChildren();  // empty the error view

    const params = {};
    for (const field of form.elements)
        if (field.name != "")
            params[field.name] = field.value;

    const response = await fetch(
        form.action, {
            method: form.method,
            body: new URLSearchParams(params)},
    );

    if (response.ok) {
        window.location.href = "/";
        return;
    }

    const json = await response.json();
    const errd = document.createElement("div");
    errd.setAttribute("class", "alert alert-danger");
    const errt = document.createTextNode("Submission failed:");
    const errp = document.createElement("pre");
    errp.textContent = JSON.stringify(json);
    errd.replaceChildren(errt, errp);
    error.replaceChildren(errd);
}
