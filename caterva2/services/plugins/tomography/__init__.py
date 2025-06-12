import ast
import pathlib

# Requirements
import jinja2
import numpy as np
import PIL.Image
from fastapi import Depends, FastAPI, Request, responses
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

# Project
from caterva2.services.sub import optional_user

from ...sub import get_container, resize_image
from ...sub import templates as sub_templates
from ...subscriber import db

app = FastAPI()
BASE_DIR = pathlib.Path(__file__).resolve().parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.loader = jinja2.ChoiceLoader(
    [
        templates.env.loader,  # Preserve the original loader
        sub_templates.env.loader,  # Add the sub-templates loader
    ]
)

name = "tomography"  # Identifies the plugin
label = "Tomography"
contenttype = "tomography"


abspath_and_dataprep = None
urlbase = None


def init(absp_n_datap, urlbase_):
    global abspath_and_dataprep
    global urlbase
    abspath_and_dataprep = absp_n_datap
    urlbase = urlbase_


def url(path: str) -> str:
    return f"{urlbase}/{path}"


def guess(path: pathlib.Path, meta) -> bool:
    """Does dataset (given path and metadata) seem of this content type?"""
    if not hasattr(meta, "dtype"):
        return False  # not an array

    dtype = meta.dtype
    if dtype is None:
        return False

    # Structured dtype
    if isinstance(dtype, str) and dtype.startswith("["):
        dtype = eval(dtype)  # TODO Make it safer

    # Sometimes dtype is a tuple (e.g. ('<f8', (10,))), and this seems a safe way to handle it
    try:
        dtype = np.dtype(dtype)
    except (ValueError, TypeError):
        dtype = np.dtype(ast.literal_eval(dtype))
    if dtype.kind != "u":
        return False

    shape = tuple(meta.shape)
    if len(shape) == 3:
        return True  # grayscale

    # RGB(A)
    return len(shape) == 4 and shape[-1] in (3, 4)


@app.get("/display/{path:path}", response_class=HTMLResponse)
async def display(
    request: Request,
    # Path parameters
    path: pathlib.Path,
    user: db.User = Depends(optional_user),
):
    ndim = 0
    i = 0

    array = await get_container(path, user)
    height, width = (x for j, x in enumerate(array.shape[:3]) if j != ndim)

    base = url(f"plugins/{name}")
    href = f"{base}/image/{path}?{ndim=}&{i=}"

    context = {
        "href": href,
        "shape": array.shape,
        "width": width,
        "height": height,
    }
    return templates.TemplateResponse(request, "display.html", context=context)


async def __get_image(path, user, ndim, i):
    # Alternatively, call abspath_and_dataprep with the corresponding slice to download data async
    array = await get_container(path, user)
    index = [slice(None) for x in array.shape]
    index[ndim] = slice(i, i + 1, 1)
    content = array[tuple(index)].squeeze()
    return PIL.Image.fromarray(content)


@app.get("/image/{path:path}")
async def image_file(
    request: Request,
    # Path parameters
    path: pathlib.Path,
    # Query parameters
    ndim: int,
    i: int,
    width: int | None = None,
    user: db.User = Depends(optional_user),
):
    img = await __get_image(path, user, ndim, i)
    img_file = resize_image(img, width)

    def iterfile():
        yield from img_file

    return responses.StreamingResponse(iterfile(), media_type="image/png")
