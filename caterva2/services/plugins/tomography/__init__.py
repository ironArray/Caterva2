import io
import pathlib

import blosc2

import numpy
from fastapi import Depends, FastAPI, Request, responses
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .. import current_active_user
from ...subscriber import db


app = FastAPI()
BASE_DIR = pathlib.Path(__file__).resolve().parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")

name = "tomography" # Identifies the plugin
label = "Tomography"
contenttype = "tomography"


abspath_and_dataprep = None

def init(absp_n_datap):
    global abspath_and_dataprep
    abspath_and_dataprep = absp_n_datap


def guess(path: pathlib.Path, meta) -> bool:
    """Does dataset (given path and metadata) seem of this content type?"""
    if not hasattr(meta, 'dtype'):
        return False  # not an array

    dtype = meta.dtype

    # Structured dtype
    if isinstance(dtype, str) and dtype.startswith('['):
        dtype = eval(dtype)  # TODO Make it safer

    dtype = numpy.dtype(dtype)
    shape = tuple(meta.shape)
    if dtype.kind != "u":
        return False
    if len(shape) == 3:
        return True  # grayscale
    if len(shape) == 4 and shape[-1] in (3, 4):
        return True  # RGB(A)
    return False


@app.get("/display/{path:path}", response_class=HTMLResponse)
def display(
    request: Request,
    # Path parameters
    path: pathlib.Path,
    user: db.User = Depends(current_active_user),
):

    abspath, _ = abspath_and_dataprep(path, user=user)
    arr = blosc2.open(abspath)

    base = f"/plugins/{name}"
    context = {
        "href": f"{base}/display_one/{path}",
        "pages": arr.shape[0],
    }
    return templates.TemplateResponse(request, "display.html", context=context)


async def __get_image(path, i, user):
    from PIL import Image

    # TODO: This accepts a slice, pass it in.
    abspath, dataprep = abspath_and_dataprep(path, user=user)
    await dataprep()
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
    user: db.User = Depends(current_active_user),
):

    img = await __get_image(path, i, user)
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
    user: db.User = Depends(current_active_user),
):

    img = await __get_image(path, i, user)

    if width and img.width > width:
        height = (img.height * width) // img.width
        img = img.resize((width, height))

    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format="PNG")
    img_byte_arr.seek(0)

    def iterfile():
        yield from img_byte_arr

    return responses.StreamingResponse(iterfile(), media_type="image/png")
