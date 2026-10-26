/**
 * Google Apps Script — the study's Drive endpoint and participant upload page.
 *
 *   - Web app URL with no parameters: the participant page (upload_page.html). The
 *     participant's browser reduces their Garmin export zip to the needed health
 *     files and calls submitExport(), which saves garmin_export_<id>.zip to Drive.
 *   - doPost: receives CSVs from the server / desktop collector (password flow).
 *   - doGet with ?token=...: lists / downloads uploads (collector/fetch_uploads.py).
 *
 * Setup:
 *   1. Create a Drive folder for the uploads; copy its ID from the URL.
 *   2. script.google.com -> New project -> paste this file, then add two HTML files
 *      (File > + > HTML) named exactly "upload_page" and "privacy_page" and paste
 *      collector/upload_page.html and collector/privacy_page.html into them.
 *   3. Fill in FOLDER_ID and UPLOAD_TOKEN below.
 *   4. Deploy -> New deployment -> type "Web app".
 *        - Execute as: Me
 *        - Who has access: Anyone
 *      After later edits: Deploy -> Manage deployments -> Edit -> Version: New
 *      version, so the URL stays the same.
 *   5. The Web app URL is the link to send participants. Use it and the same
 *      UPLOAD_TOKEN for GARMIN_ENDPOINT_URL / GARMIN_UPLOAD_TOKEN.
 */

var FOLDER_ID = "PASTE_YOUR_DRIVE_FOLDER_ID_HERE";
var UPLOAD_TOKEN = "PASTE_THE_SAME_SHARED_SECRET_HERE";

// Files a participant upload may contain. Keep in sync with EXPORT_FILES in
// collector/garmin_export.py and upload_page.html.
var EXPORT_FILES = [
  /DI-Connect-Aggregator\/UDSFile_[^/]*\.json$/,
  /DI-Connect-Wellness\/[^/]*_sleepData\.json$/,
  /DI-Connect-Wellness\/[^/]*_healthStatusData\.json$/,
  /DI-Connect-Wellness\/[^/]*_MenstrualCycles\.json$/,
  /DI-Connect-Wellness\/[^/]*_DailyMenstrualLogs\.json$/,
  /DI-Connect-Wellness\/[^/]*_LifestyleLogging\.json$/,
  /DI-Connect-Metrics\/MetricsAcuteTrainingLoad_[^/]*\.json$/,
  /DI-Connect-Metrics\/TrainingHistory_[^/]*\.json$/,
  /DI-Connect-Metrics\/MetricsMaxMetData_[^/]*\.json$/,
  /DI-Connect-Metrics\/MetricsHeatAltitudeAcclimation_[^/]*\.json$/
];
var META_FIELDS = ["age_range", "contraception", "cycle_regularity", "device_model",
                   "wears_overnight", "conditions"];
var MAX_UPLOAD_BYTES = 40 * 1024 * 1024;

function doPost(e) {
  try {
    var p = e.parameter;

    if (!p || p.token !== UPLOAD_TOKEN) {
      return _text("forbidden");
    }
    if (!p.data) {
      return _text("error: no data");
    }

    var bytes = Utilities.base64Decode(p.data);
    var filename = p.filename || ("garmin_" + new Date().getTime() + ".csv");
    var blob = Utilities.newBlob(bytes, "text/csv", filename);

    DriveApp.getFolderById(FOLDER_ID).createFile(blob);
    return _text("ok");
  } catch (err) {
    return _text("error: " + err);
  }
}

/**
 * doGet — the participant page, or (with a token) list / download the uploads.
 *   (no parameters)           -> participant upload page
 *   ?page=privacy             -> privacy policy
 *   ?token=...                -> JSON [{id, name, size}, ...] for every upload
 *   ?token=...&id=...         -> JSON {name, data(base64)} for that one upload
 * The token variants are used by collector/fetch_uploads.py. Files are served one
 * per request so the response size doesn't grow with the number of uploads.
 */
function doGet(e) {
  var p = (e && e.parameter) || {};
  if (p.token === undefined) {
    var page = HtmlService.createTemplateFromFile(p.page === "privacy" ? "privacy_page" : "upload_page");
    page.appUrl = ScriptApp.getService().getUrl();
    return page.evaluate()
               .setTitle("Garmin Cycle Study")
               .addMetaTag("viewport", "width=device-width, initial-scale=1");
  }
  try {
    if (p.token !== UPLOAD_TOKEN) {
      return _text("forbidden");
    }
    if (p.id) {
      var file = DriveApp.getFileById(p.id);
      if (!_inUploadFolder(file)) {
        return _text("forbidden");
      }
      return _json({ name: file.getName(),
                     data: Utilities.base64Encode(file.getBlob().getBytes()) });
    }
    var files = DriveApp.getFolderById(FOLDER_ID).getFiles();
    var out = [];
    while (files.hasNext()) {
      var f = files.next();
      out.push({ id: f.getId(), name: f.getName(), size: f.getSize() });
    }
    return _json(out);
  } catch (err) {
    return _text("error: " + err);
  }
}

/**
 * Called from upload_page.html with the questionnaire answers and the reduced
 * export (base64 zip). Re-checks the contents, adds metadata.json and saves
 * garmin_export_<participant_id>.zip to the Drive folder. Returns the ID.
 */
function submitExport(meta, zipBase64) {
  if (!meta || meta.consent !== true) {
    throw new Error("Please tick the consent box to continue.");
  }
  var bytes = Utilities.base64Decode(zipBase64);
  if (bytes.length > MAX_UPLOAD_BYTES) {
    throw new Error("The upload is unexpectedly large. Please contact the research team.");
  }
  var files = Utilities.unzip(Utilities.newBlob(bytes, "application/zip"));
  var unexpected = files.filter(function (f) {
    return !EXPORT_FILES.some(function (re) { return re.test(f.getName()); });
  });
  if (!files.length || unexpected.length) {
    throw new Error("The upload did not contain the expected Garmin files.");
  }

  var participantId = Utilities.getUuid().replace(/-/g, "").slice(0, 8);
  var metadata = { participant_id: participantId, source: "garmin_export",
                   submitted_at: new Date().toISOString() };
  META_FIELDS.forEach(function (k) { metadata[k] = String(meta[k] || "").slice(0, 200); });
  files.push(Utilities.newBlob(JSON.stringify(metadata, null, 2), "application/json", "metadata.json"));

  DriveApp.getFolderById(FOLDER_ID)
          .createFile(Utilities.zip(files, "garmin_export_" + participantId + ".zip"));
  return participantId;
}

// Only files in the uploads folder may be downloaded, not any Drive file by ID.
function _inUploadFolder(file) {
  var parents = file.getParents();
  while (parents.hasNext()) {
    if (parents.next().getId() === FOLDER_ID) {
      return true;
    }
  }
  return false;
}

function _json(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
                       .setMimeType(ContentService.MimeType.JSON);
}

function _text(s) {
  return ContentService.createTextOutput(s).setMimeType(ContentService.MimeType.TEXT);
}
