import io
import pathlib

import blosc2

from fastapi import FastAPI, Request, responses
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ... import srv_utils


app = FastAPI()
BASE_DIR = pathlib.Path(__file__).resolve().parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")

name = "tomography" # Identifies the plugin
contenttype = "tomography"


cache = None
partial_download = None

def init(sub_cache, f):
    #print("INIT", sub_cache)
    global cache
    cache = sub_cache
    global partial_download
    partial_download = f

@app.get("/display/{path:path}", response_class=HTMLResponse)
def display(
    request: Request,
    # Path parameters
    path: pathlib.Path,
):

    abspath = srv_utils.cache_lookup(cache, cache / path)
    arr = blosc2.open(abspath)

    base = f"/plugins/{name}"
    context = {
        "href": f"{base}/display_one/{path}",
        "pages": arr.shape[0],
    }
    return templates.TemplateResponse(request, "display.html", context=context)


async def __get_image(path, i):
    from PIL import Image

    abspath = srv_utils.cache_lookup(cache, cache / path)
    await partial_download(abspath, str(path))
    arr = blosc2.open(abspath)

    img = arr[i,:]
    return Image.fromarray(img)


@app.get("/display_one/{path:path}", response_class=HTMLResponse)
async def display_one(
    request: Request,
    # Path parameters
    path: pathlib.Path,
    # Query parameters
    i: int,
):

    img = await __get_image(path, i)
    width = 768  # Max size

    base = f"/plugins/{name}"

    links = []
    if width and img.width > width:
        links.append({
            "href": f"{base}/image/{path}?i={i}",
            "label": f"{img.width} x {img.height} (original size)",
            "target": "blank_",
        })

    context = {
        "src": f"{base}/image/{path}?{i=}&{width=}",
        "links": links
    }
    return templates.TemplateResponse(request, "display_one.html", context=context)


@app.get("/image/{path:path}")
async def image_file(
    request: Request,
    # Path parameters
    path: pathlib.Path,
    # Query parameters
    i: int,
    width: int | None = None,
):

    img = await __get_image(path, i)

    if width and img.width > width:
        height = (img.height * width) // img.width
        img = img.resize((width, height))

    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format="PNG")
    img_byte_arr.seek(0)

    def iterfile():
        yield from img_byte_arr

    return responses.StreamingResponse(iterfile(), media_type="image/png")
