---
name: fork-scope
description: What this openpilot fork is allowed to change relative to commaai/master, and how to measure the current deviation. Use before adding a param, a manager predicate, a new file, or any change to an upstream file, and when auditing drift or deciding whether something belongs here at all. Triggers on "does this belong in the fork", "how far have we drifted", "diff against upstream", "add a param", "should we change process_config".
---

# fork-scope

This is a comma 4 ("mici") fork that acts as a **standalone data recorder**. It never uploads
anything to comma. The stated target is that its *entire* diff vs `commaai/master` is four
things:

1. **CAN / OBD** — the passive car port, panda-firmware CAN ignition, holding OBD multiplexing on
2. **The mici onroad view** — wide cam at low speed, driver-camera page, capture status line, no
   dashcam-mode banner
3. **SMB upload in settings** — local NAS only
4. **Comma uploads disabled** — `uploader` and `manage_athenad` off, firehose removed

Anything else is drift. Push back on adding to it, including your own changes.

## Concrete rules

- **No new params.** Every param is a deviation in `common/params_keys.h` and a lifetime to
  reason about. The `Smb*` keys are the exception and are already spent. If you need per-drive
  state, derive it — elapsed capture time comes from `ui_state.started_time`, not a fork param.
- **No new manager predicates.** `system/manager/process_config.py` must stay byte-identical to
  upstream apart from `enabled=False` on `manage_athenad` and `uploader`. A predicate that reads
  a param runs on **every manager loop** for every proc.
- **Prefer additive files over edits to upstream files.** A new module in
  `selfdrive/ui/mici/onroad/` costs nothing on rebase. Three edited lines in
  `system/ui/widgets/__init__.py` cost a conflict every time upstream touches it.
- **Delete fork code the moment its caller goes.** When the manual recorder was removed, the
  `cover=True` crop in `cameraview.py` and `BigTileButton` in `button.py` had no callers left;
  both went back to upstream byte-for-byte.
- **A workaround for a test belongs in fork code, not upstream code.** The xattr `EOPNOTSUPP`
  tolerance existed so `smb_upload.demo()` could run on tmpfs. It lived in upstream's
  `xattr_cache.py`; it now lives in `smb_upload.RouteFile.done`, which is ours.

## Measuring the deviation

The fork branched from upstream commit `c21b0821d`. Diff scoped to source, or it will time out
on submodule churn:

```bash
git diff --stat c21b0821d HEAD -- openpilot/ opendbc_repo panda/
git diff c21b0821d HEAD -- <one file>          # inspect a specific deviation
git log --oneline c21b0821d..HEAD              # the fork's own commits
```

Sort every changed file into one of the four buckets. Anything that doesn't fit is either drift
to revert or a bucket you didn't know you had.

**Caveat:** `c21b0821d` is old enough that some diff lines are just *adaptations to a newer
opendbc* — `CP.startingState` → `CP.deprecated.startingState`, `carState.gasDEPRECATED` →
`carState.deprecated.gas`. Those would vanish against a current `commaai/master`. Fetch upstream
before trusting a drift list:

```bash
git fetch upstream                              # slow; run it in the background
git diff --stat upstream/master HEAD -- openpilot/
```

## Where the deviations actually are

| Bucket | Files |
|---|---|
| CAN/OBD | `opendbc_repo` (submodule: brand, DBC, `safety/ignition.h`), `selfdrive/pandad/pandad.cc` |
| Onroad UI | `ui/mici/onroad/{status_line.py,augmented_road_view.py,driver_camera_dialog.py}`, `ui/mici/layouts/main.py`, `selfdrived/selfdrived.py`, `ui/ui_state.py` |
| SMB | `system/loggerd/smb_upload.py`, `ui/mici/layouts/upload.py`, `common/params_keys.h`, `system/ui/widgets/{__init__.py,nav_widget.py}`, `ui/mici/layouts/settings/settings.py` |
| No uploads | `system/manager/process_config.py`, `ui/mici/layouts/settings/firehose.py` (deleted) |

The two `system/ui/widgets/` edits are the ones to watch — they're shared framework code:
`nav_widget.py` forwards `**kwargs` down the MRO so `NavScroller(NavWidget, Scroller)` can pass
Scroller options, and `__init__.py` propagates touch-validity to nested children so a swipe
across a page can't press buttons inside it.

## Things deliberately NOT done

- **The manual `Recording` mode is gone** (deleted 2026-07-21). CAN ignition works, so the key
  starts the recording by itself. Recover it from history if ever needed:
  `git show d95605a00:openpilot/selfdrive/ui/mici/layouts/recorder.py`.
- **`tombstoned`/sentry left alone** — already inert. `system/sentry.py` requires
  `"commaai" in git_origin`, and this fork's origin is `goldbattle`. No change needed to stop it
  phoning home.
- **The big-UI path (`selfdrive/ui/layouts/`) left alone**, firehose included. `ui.py` only
  builds it when `gui_app.big_ui()`, which is false on mici. Editing dead code adds diff for
  nothing.
- **`DisablePowerDown` not set.** The low-voltage shutdown is the only working battery
  protection on this hardware (the capacity trigger is dead — mici has no power sensor), and
  disabling it would flatten the car battery.

## Related

`dev-loop`, `comma-device`, `car-port-passive`, `local-dev`, `route-replay`.
