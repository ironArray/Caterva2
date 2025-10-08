(Using-the-REST-API)=
# Using the REST API
Using the ``curl`` command in the terminal, we can access the Caterva2 REST API and query for example the list of available roots:
```
curl -X 'GET' \
  'https://cat2.cloud/demo/api/roots' \
  -H 'accept: application/json'
```

```json
{
  "@public": {
    "name": "@public"
  }
}
```

We can query the list of files in the `@public` root via:

```
curl -X 'GET' \
  'https://cat2.cloud/demo/api/list/@public' \
  -H 'accept: application/json'
```

which gives:

```
[
  "examples/README.md",
  "examples/Wutujing-River.jpg",
  "examples/cat2cloud-brochure.pdf",
  "examples/dir1/ds-2d.b2nd",
...
  "examples/tomo-guess-test.b2nd",
  "la_blosclz.b2nd"
]
```
Let's get some metadata for the `"examples/dir1/ds-2d.b2nd"` dataset:
```
curl -X 'GET' \
  'https://cat2.cloud/demo/api/info/@public/examples/dir1/ds-2d.b2nd' \
  -H 'accept: application/json'
```
```json
{
  "shape": [
    10,
    20
  ],
  "chunks": [
    5,
    5
  ],
  "blocks": [
    2,
    3
  ],
  "dtype": "uint16",
  "schunk": {
    ...
  },
  "mtime": "2025-10-05T22:00:10.944111Z"
}

```
Finally we can download the dataset to a local file, `localfile.b2nd`, using:
```
curl -X 'GET' \
'https://cat2.cloud/demo/api/download/@public/examples/dir1/ds-2d.b2nd' \
  -H 'accept: application/json' --output "localfile.b2nd"
```
For further details see the [Caterva2 REST API documentation](https://cat2.cloud/demo/docs), which can be used to automatically generate ``curl`` commands of the kind in this tutorial.
