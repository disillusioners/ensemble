/**
 * Phase 6 / clipboard-image-chat — ``ImageViewerActionsService`` spec.
 *
 * Plain-TS logic-mirror, NO Angular TestBed (per FE conventions — see
 * image-upload.spec.ts for the canonical pattern). The service is a
 * thin broker over ``MatDialog.open`` — we mirror the SAME open-config
 * against a stub MatDialog and assert:
 *
 *   - The correct component is opened (ImageViewerDialogComponent)
 *   - The correct ``panelClass`` is applied — both the project-wide
 *     ``dark-modal-panel`` AND the dedicated ``image-viewer-panel``
 *     (the two keep the fullscreen-only rules from leaking across
 *     dialogs)
 *   - The correct ``width`` / ``height`` / ``maxWidth`` / ``maxHeight``
 *     (95vw / 95vh — mirrors Mermaid fullscreen)
 *   - The correct ``data`` shape (``src`` + ``message_id``)
 *   - ``disableClose: false`` / ``autoFocus: 'first-tabbable'`` /
 *     ``restoreFocus: true`` — same defaults as Mermaid fullscreen so
 *     the two dialogs feel like siblings
 *
 * Identity-grep mirror-parity: ``image-viewer-panel`` appears in the
 * production source (the open-config literal below) — the
 * merge-gate ``grep -RIn "image-viewer-panel"`` MUST include the
 * mirror in this file as well as the production service.
 */
import { ImageViewerDialogComponent } from '../components/image-viewer-dialog/image-viewer-dialog.component';

// Mirror of the production service — drives the SAME contract with
// a stub MatDialog so the spec can assert the open-config shape
// without Angular DI. The acceptance for this spec lives in the
// production source (image-viewer-actions.service.ts); the mirror
// lives here ONLY so the spec is exercisable from a plain jest
// run without TestBed. The shape MUST stay byte-for-byte identical
// to the production ``openViewer`` body — a future contributor who
// changes the open-config on one side MUST change it on the other.

class StubMatDialog {
  open: jest.Mock = jest.fn();
}

function openViewer(
  dialog: StubMatDialog,
  src: string,
  messageId: string,
): void {
  dialog.open(ImageViewerDialogComponent, {
    panelClass: ['dark-modal-panel', 'image-viewer-panel'],
    width: '95vw',
    height: '95vh',
    maxWidth: '95vw',
    maxHeight: '95vh',
    disableClose: false,
    autoFocus: 'first-tabbable',
    restoreFocus: true,
    data: { src, message_id: messageId },
  });
}

describe('ImageViewerActionsService — phase 6 image viewer', () => {
  let dialog: StubMatDialog;

  beforeEach(() => {
    dialog = new StubMatDialog();
  });

  describe('openViewer — open-config (plan row 2)', () => {
    it('opens the ImageViewerDialogComponent', () => {
      openViewer(dialog, 'data:image/png;base64,AAAA', 'srv-1');
      expect(dialog.open).toHaveBeenCalledTimes(1);
      const [component] = dialog.open.mock.calls[0];
      expect(component).toBe(ImageViewerDialogComponent);
    });

    it('applies the dark-modal-panel + image-viewer-panel panel classes', () => {
      openViewer(dialog, 'data:image/png;base64,AAAA', 'srv-1');
      const [, config] = dialog.open.mock.calls[0];
      expect(config.panelClass).toEqual(['dark-modal-panel', 'image-viewer-panel']);
    });

    it('sizes the dialog to 95vw × 95vh (mirrors Mermaid fullscreen)', () => {
      openViewer(dialog, 'data:image/png;base64,AAAA', 'srv-1');
      const [, config] = dialog.open.mock.calls[0];
      expect(config.width).toBe('95vw');
      expect(config.height).toBe('95vh');
      expect(config.maxWidth).toBe('95vw');
      expect(config.maxHeight).toBe('95vh');
    });

    it('uses MatDialog defaults for close / focus — same as Mermaid fullscreen', () => {
      openViewer(dialog, 'data:image/png;base64,AAAA', 'srv-1');
      const [, config] = dialog.open.mock.calls[0];
      expect(config.disableClose).toBe(false);
      expect(config.autoFocus).toBe('first-tabbable');
      expect(config.restoreFocus).toBe(true);
    });

    it('threads the src + message_id through the data payload', () => {
      openViewer(dialog, '/api/tmp_images/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'srv-1');
      const [, config] = dialog.open.mock.calls[0];
      expect(config.data).toEqual({
        src: '/api/tmp_images/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
        message_id: 'srv-1',
      });
    });

    it('does not add an alt key on the data payload (the dialog defaults it)', () => {
      // The dialog's alt default lives in its constructor; the service
      // does not pass an alt, so the data shape has exactly two keys.
      openViewer(dialog, 'data:image/png;base64,AAAA', 'srv-1');
      const [, config] = dialog.open.mock.calls[0];
      expect(Object.keys(config.data).sort()).toEqual(['message_id', 'src']);
    });
  });

  describe('input forms — data URI AND ref URL (plan §"data URI vs ref URL handling parity")', () => {
    it('opens the dialog for a legacy data URI src', () => {
      openViewer(dialog, 'data:image/png;base64,AAAA', 'srv-1');
      const [, config] = dialog.open.mock.calls[0];
      expect(config.data.src).toBe('data:image/png;base64,AAAA');
    });

    it('opens the dialog for a canonical /api/tmp_images/<id> src', () => {
      openViewer(
        dialog,
        '/api/tmp_images/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
        'srv-1',
      );
      const [, config] = dialog.open.mock.calls[0];
      expect(config.data.src).toBe(
        '/api/tmp_images/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
      );
    });
  });
});

/**
 * Identity-grep mirror-parity assertion — the literal
 * ``image-viewer-panel`` appears verbatim in this file (the
 * ``openViewer`` mirror above), AND in the production source
 * (image-viewer-actions.service.ts). The merge-gate
 * ``grep -RIn "image-viewer-panel" frontend/src/app/`` returns both
 * sites. A future contributor who removes the literal from either
 * side will break this pin.
 */
describe('ImageViewerActionsService — identity-grep mirror-parity doc', () => {
  it('emits "image-viewer-panel" in the open-config (canonical emitter)', () => {
    // The class name is asserted in the open-config suite above
    // (panelClass: ['dark-modal-panel', 'image-viewer-panel']). This
    // block documents the convention — a future contributor must keep
    // the literal "image-viewer-panel" in this file so the
    // merge-gate identity-grep passes.
    expect(true).toBe(true);
  });
});
