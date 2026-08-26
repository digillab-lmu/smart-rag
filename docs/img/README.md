# Screenshots

Referenced from the project README and from `docs/operations-guide.md`.

## Naming

`<page>.png`, lower case, no spaces. The names below are the ones the
documentation links to; a file added under a different name is not shown
anywhere.

| File | Page | What has to be visible |
|------|------|------------------------|
| `dashboard.png`      | Agents            | The ten slots with status and the *In the map* column |
| `slot.png`           | One agent         | Archetype, name, the filled content fields |
| `upload.png`         | Add documents     | Form with slot, file, title, authors, year |
| `documents.png`      | Vector DB         | Document list with chunk counts and the *In the map* column |
| `graph.png`          | Knowledge graph   | The page in its normal state: the concepts and prerequisites the course already has, with the build controls above |
| `graph-review.png`   | Knowledge graph   | The editable proposal: tick boxes, names, prerequisites |
| `courses.png`        | Courses           | The course list with retention dates |
| `users.png`          | Accounts          | The account list with the editable name and address fields, roles and course assignments |
| `learners.png`       | People            | The lookup form and a result across the four systems |
| `status.png`         | System status     | The live check with at least one item not yet green |

## Requirements for the images

- Width 1400 to 1800 pixels, PNG. A screenshot taken on a HiDPI display of a
  window around 900 pixels wide lands in that range on its own. The images are
  displayed at 260 pixels in the README and open at full size when clicked, so
  the width matters for reading the enlarged view, not for the layout.
- Expect 300 to 800 KB per file. These are binaries: replacing one adds
  another object to the history rather than shrinking the old one, so replace
  rather than accumulate.
- Browser window without extensions or bookmarks bar.
- **No real data.** A test course with invented documents and an invented
  learner id. Screenshots reach GitHub and stay there.
- Same course and same language across all images, so the set reads as one
  installation.

## How they are embedded

GitHub renders no JavaScript, so there is no slider and no lightbox. A
thumbnail linking to the full image is the available mechanism:

```markdown
[<img src="docs/img/upload.png" width="360">](docs/img/upload.png)
```
