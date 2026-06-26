import { JupyterFrontEnd, JupyterFrontEndPlugin } from '@jupyterlab/application';
import { PageConfig } from '@jupyterlab/coreutils';
import { Contents } from '@jupyterlab/services';

/**
 * Mirror every JupyterLite notebook/file save back to the caterva2 server's
 * `api/upload/<path>` endpoint.
 *
 * Vanilla JupyterLite only persists saves to the browser's local storage, so
 * without this plugin edits never reach the caterva2 server. This replaces the
 * old `ironArray/jupyterlite` fork patch (`c2upload` in `drive.ts`), letting the
 * deployment build from stock upstream JupyterLite instead of a fork.
 */
const plugin: JupyterFrontEndPlugin<void> = {
  id: 'caterva2-save:plugin',
  description: 'Mirror JupyterLite saves back to the caterva2 server.',
  autoStart: true,
  activate: (app: JupyterFrontEnd): void => {
    const { contents } = app.serviceManager;
    const originalSave = contents.save.bind(contents);

    // Wrap the contents manager's save: let the normal (local) save happen,
    // then fire-and-forget an upload to the caterva2 server.
    (contents as { save: Contents.IManager['save'] }).save = async (
      path: string,
      options?: Partial<Contents.IModel>
    ): Promise<Contents.IModel> => {
      const model = await originalSave(path, options);
      void uploadToCaterva2(path, options?.content ?? model.content);
      return model;
    };
  }
};

async function uploadToCaterva2(path: string, content: unknown): Promise<void> {
  if (content === null || content === undefined) {
    return;
  }
  const filename = path.split('/').pop() || path;
  const payload = typeof content === 'string' ? content : JSON.stringify(content);

  const body = new FormData();
  body.append('file', new Blob([payload]), filename);

  try {
    const res = await fetch(caterva2UploadUrl(path), {
      method: 'POST',
      body,
      credentials: 'same-origin' // reuse the caterva2 session cookie for auth
    });
    if (!res.ok) {
      console.error(
        `caterva2 save failed for ${path}: ${res.status} ${res.statusText}`
      );
    }
  } catch (err) {
    console.error(`caterva2 save failed for ${path}`, err);
  }
}

/**
 * The JupyterLite app is served at `<root>/static/jupyterlite/`; the caterva2
 * upload API lives at `<root>/api/upload/<path>`. Resolve it *relative* to the
 * app base URL (two levels up) so it keeps working behind an nginx path prefix.
 */
function caterva2UploadUrl(path: string): string {
  const base = new URL(PageConfig.getBaseUrl(), window.location.href);
  return new URL(`../../api/upload/${encodeURI(path)}`, base).toString();
}

export default plugin;
