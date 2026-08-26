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
| `graph.png`          | Knowledge graph   | The diagram of a proposal, with the concept table below |
| `graph-review.png`   | Knowledge graph   | The editable proposal: tick boxes, names, prerequisites |
| `courses.png`        | Courses           | The course list with retention dates |
| `learners.png`       | People            | The lookup form and a result across the four systems |
| `status.png`         | System status     | The live check with at least one item not yet green |

## Requirements for the images

- Width 1400 to 1600 pixels, PNG. They are displayed at 360 pixels in the
  README and open at full size when clicked.
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
