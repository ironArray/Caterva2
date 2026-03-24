Releasing a version
===================

Preliminaries
-------------

- Make sure that ``RELEASE_NOTES.md`` and ``ANNOUNCE.md`` are up to
  date with the latest news in the release.

- Check that ``__version__`` symbol in ``caterva2/__init__.py`` contains the correct info.

- Commit the changes with::

    $ git commit -a -m "Getting ready for release X.Y.Z"
    $ git push

For the release number, use the current date, but do not use 0's
(e.g. 2025.4.9, 2025.12.1, etc.).

Testing
-------

Follow the steps in ``README-DEVELOPERS.md`` file for locally creating and
installing the wheel, then test it::

  $ python -m pip install -e ".[tests,hdf5]"
  $ cd ..   # to avoid using the source code
  $ python -m caterva2.tests -v
  $ CATERVA2_SECRET="c2sikrit" python -m caterva2.tests -v
  $ cd -

Check that the examples in docstrings are up to date. You will
need to register a user in https://cat2.cloud/demo/ with
username 'user@example.com' and password 'foo'. Then, copy
the content of ``caterva2.sample.toml`` to ``caterva2.toml``
and run the following commands::

  $ rm -r _caterva2/
  $ cat2-admin adduser superuser@example.com foobarfoo -S
  $ CATERVA2_SECRET="123" cat2-server &

And experiment a bit with uploading, browsing and downloading files.

If the tests pass, you are ready to release.


Staging wheel channel
---------------------

Before publishing a production wheel for all JupyterLite users, you can publish
a staging wheel to a separate GitHub-hosted channel.  This is useful for testing
changes that affect the browser-side wheel installation, such as new Pyodide
functionality or notebook helpers, without changing the production
``wheels/latest.txt`` pointer.

The wheel publishing workflow supports two channels:

- production: ``https://ironarray.github.io/Caterva2/wheels/``
- staging: ``https://ironarray.github.io/Caterva2/wheels-staging/``

Each channel gets its own ``latest.txt`` file:

- production: ``https://ironarray.github.io/Caterva2/wheels/latest.txt``
- staging: ``https://ironarray.github.io/Caterva2/wheels-staging/latest.txt``

The staging channel is published by manually running the
``Build and Publish Python Wheels for Caterva2`` workflow with
``channel=staging``.

To do a staging release:

- Push the branch you want to test to GitHub.

- Open the workflow page for
  ``Build and Publish Python Wheels for Caterva2``.

- Click ``Run workflow``.

- Select the branch to build.

- Select ``channel=staging``.

- Run the workflow.

After it finishes, the built wheel will be available under
``wheels-staging/`` and will not modify the production ``wheels/`` channel.

The workflow also publishes these helper files in the selected channel:

- ``latest.txt``: latest wheel filename in that channel
- ``commit.txt``: commit SHA used to build the wheel
- ``ref.txt``: Git ref name used to build the wheel
- ``channel.txt``: published channel name

Testing a staging wheel from JupyterLite
----------------------------------------

For notebook testing, point the Pyodide install to the staging channel instead
of the production one.  For example::

  import sys
  if sys.platform == "emscripten":
      import requests
      import micropip

      caterva_latest_url = "https://ironarray.github.io/Caterva2/wheels-staging/latest.txt"
      caterva_wheel_name = requests.get(caterva_latest_url).text.strip()
      caterva_wheel_url = f"https://ironarray.github.io/Caterva2/wheels-staging/{caterva_wheel_name}"
      await micropip.install(caterva_wheel_url)
      print(f"Installed staging wheel: {caterva_wheel_name}")

Use a fresh browser tab or kernel when testing a new staging wheel, so Pyodide
does not reuse a previously installed package from the same session.


Check documentation
-------------------

Check that the ``README.md`` and ``README-DEVELOPERS.md`` are consistent with this new release.


Tagging and releasing
---------------------

- Create a tag ``X.Y.Z`` from ``main``.  Use the next message::

    $ git tag -a vX.Y.Z -m "Tagging version X.Y.Z"

- Push the tag to the github repo::

    $ git push --tags

- If you happen to have to delete the tag, such as artifacts demonstrates a fault, first delete it locally::

    $ git tag --delete vX.Y.Z

  and then remotely on Github::

    $ git push --delete origin vX.Y.Z

- Create a new release by visiting https://github.com/ironArray/Caterva2/releases/new
  and add the release notes copying them from ``RELEASE_NOTES.md`` document.

Check that the release is available at https://pypi.org/project/caterva2/ and test it with::

  pip uninstall caterva2
  pip install caterva2[tests]
  python -m caterva2.tests
  CATERVA2_SECRET="c2sikrit" python -m caterva2.tests

Announcing
----------

- Send an announcement to the blosc and comp.compression mailing lists.
  Use the ``ANNOUNCE.md`` file as skeleton (likely as the definitive version).

- Announce the release in Linkedin/mastodon/bluesky from the @Blosc2 account.


Post-release actions
--------------------

- Edit ``__version__`` var in ``caterva2/__init__.py`` to increment the
  version to the next minor one (i.e. X.Y.Z --> X.Y.(Z+1).dev0).

- Create new headers for adding new features in ``RELEASE_NOTES.md``
  and add this place-holder instead::

    #XXX version-specific blurb XXX#

- Commit the changes::

  $ git commit -a -m"Post X.Y.Z release actions done"
  $ git push

That's all folks!
