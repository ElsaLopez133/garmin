/**
 * Google Apps Script — receives uploaded Garmin CSVs and saves them to a Drive folder.
 *
 * Deploy steps are in BUILD.md. In short:
 *   1. Create a Drive folder for the uploads; copy its ID from the URL.
 *   2. script.google.com -> New project -> paste this file.
 *   3. Fill in FOLDER_ID and UPLOAD_TOKEN below.
 *   4. Deploy -> New deployment -> type "Web app".
 *        - Execute as: Me
 *        - Who has access: Anyone
 *   5. Copy the Web app URL into garmin_collector.py (ENDPOINT_URL), and use the
 *      same UPLOAD_TOKEN in both files.
 */

var FOLDER_ID = "PASTE_YOUR_DRIVE_FOLDER_ID_HERE";
var UPLOAD_TOKEN = "PASTE_THE_SAME_SHARED_SECRET_HERE";

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

function _text(s) {
  return ContentService.createTextOutput(s).setMimeType(ContentService.MimeType.TEXT);
}
