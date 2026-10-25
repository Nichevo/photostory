# PhotoStory

Makes a short PDF of the trips you took together, from your own photos: a cover page with an overview
map and a list of trips, then one page per trip with a map of the places you visited, the dates, how many
nights, and a few photos of the two of you.

Everything runs on your computer. Your photos are not uploaded anywhere. The only things fetched from
the internet are map tiles and place names (OpenStreetMap), and the face models the first time.

## What it looks like

These screenshots use made-up demo photos and names. In the real app the maps are OpenStreetMap street maps.

| The cover page | Where you went | A page per trip |
| --- | --- | --- |
| <img src="docs/screenshots/pdf-cover.png" width="260" alt="The cover page with a photo, an overview map, the trips and highlights"> | <img src="docs/screenshots/pdf-maps.png" width="260" alt="A page of small maps, one per trip"> | <img src="docs/screenshots/pdf-trip.png" width="260" alt="A trip page with a map, a highlight, a short story and captioned photos"> |

**Choose your photos and dates**

<img src="docs/screenshots/app-setup.png" width="800" alt="The setup screen: photo folder, reference photos, dates and home town">

**Tick the trips you want from the ones it found**

<img src="docs/screenshots/app-pick.png" width="800" alt="The list of trips found, with tick boxes, dates, nights and places">

**Check each trip, swap photos and mark highlights** (green tags mark photos where both of you were recognised)

<img src="docs/screenshots/app-review.png" width="800" alt="Reviewing a trip: the chosen photos and other photos to pick from">

## Start it

1. Install Python 3 from <https://www.python.org/downloads/> if you don't have it
   (on Windows, tick "Add Python to PATH" in the installer).
2. Double-click **start-mac.command** (Mac) or **start-windows.bat** (Windows).
   The first run takes a minute or two to install what it needs. On a Mac, if it says the file can't be
   opened, right-click it, choose Open, then Open again.
3. Your browser opens the PhotoStory page.

### Or with Conda

If you use Anaconda or Miniconda, you can keep everything in its own Conda environment instead.
Open the **Anaconda Prompt**, go to the PhotoStory folder, and run this once:

```
conda env create -f environment.yml
```

Then, to start PhotoStory, double-click **start-conda.bat**, or run this in the Anaconda Prompt:

```
conda activate photostory
python -m photostory
```

To remove it all later, run `conda env remove -n photostory` and delete the folder.

## Use it

PhotoStory keeps each document as a **story**. Pick one from the Story list at the top, or click
**New story**. Everything in a story is saved as you go (trips, ticks, photos, captions, highlights,
map areas), so you can close the app and carry on later.

1. **Photo folder**: your Dropbox photos, e.g. `Dropbox/Camera Uploads`. Subfolders are included.
   If Dropbox keeps files "online-only", PhotoStory will make Dropbox download every photo it reads,
   so pick a folder that's available offline, or expect the first scan to take a while.
2. **Reference photos** (optional but recommended): make a folder like this
   ```
   people/
     Alex/    5 to 10 photos with only Alex's face in them
     Sam/     5 to 10 photos with only Sam's face in them
   ```
   Use clear photos from different years and angles. The subfolder names are used as your names.
   With these, PhotoStory picks photos where both of you are recognised. Without them, it picks sharp
   photos spread across each trip and you choose.
3. **From / To**: the date range to cover. Leave both blank to look through every photo.
4. **Home**: your town, e.g. `Bristol, UK`. Anything more than 50 km from home counts as a trip.
   Add a line for each place if you've lived in more than one.
5. Click **Find trips**. The first scan of a big folder can take a while; later scans are fast.
   You get a list of the trips it found, with dates, nights and places. If a trip is missing, open
   **Photos by month** to see whether those photos have locations.
   Next time you open the story the settings are folded away; click **Refresh trips** only when you've
   added photos or changed a setting. Your choices are kept.
6. A trip that covers more than one area (e.g. Shanghai then Beijing) can be split with
   **Split into N**, one trip per area, or joined back later.
7. Tick the trips you want, then click **Choose photos for the ticked trips**. Photos are only
   analysed for the trips you tick, so this stays quick even if you scanned years of photos.
8. If PhotoStory isn't sure about some faces, it asks **Is this Alex?** Answer and it remembers,
   for this and every later story. You can also add more reference photos and click
   **Check faces again**.
9. Check each trip: rename it, click photos to add or remove them, give photos captions, and click
   **Make highlight** for special moments such as getting engaged or dinner with family.
   **Write captions and story** drafts a caption for each photo and a short story of the places you
   visited each day, from where the photos were taken. It's all editable.
10. Open **Map: change the area shown** to zoom or move a trip's map, and **Cover page** to choose the
   cover photo and the overview map's area.
11. Click **Make PDF**. PDFs are saved in the story's folder under `PhotoStory/stories` in your home folder.

## How trips are worked out

* Each photo's date, time and GPS position come from the photo file itself (EXIF), the data your phone
  records when you take it. Photos without a date are skipped.
* A trip is a run of photos taken away from home, with no gap of more than 36 hours. Trips shorter than
  one night are skipped unless you change "Shortest trip" to 0.
* Within a trip, photos within 30 km of each other are one stop. The nights at each stop are guessed from
  where the last photo of each day was taken.
* Photos without GPS, taken during a trip, are included in that trip.

## Command line (optional)

```
python -m photostory scan --photos "~/Dropbox/Camera Uploads" --people ~/PhotoStory/people \
    --from 2023-01-01 --to 2025-12-31 --home "Bristol, UK"
python -m photostory pdf
```
