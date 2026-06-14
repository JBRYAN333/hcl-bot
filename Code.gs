/**
 * HCL Bot — Google Sheets Backup via Supabase
 *
 * Como usar:
 *   1. Abra a planilha: https://docs.google.com/spreadsheets/d/1Iv9DpIonCF8zzJ-SDszMdbRJnAQBNlGhYpHimRRzkdY/edit
 *   2. Extensions > App Script
 *   3. Cole este código e salve (Ctrl+S)
 *   4. No menu esquerdo: Project Settings > Script Properties
 *      - SUPABASE_URL: https://eutlqudgumdxrofoemms.supabase.co/rest/v1
 *      - SUPABASE_KEY: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImV1dGxxdWRndW1keHJvZm9lbW1zIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4MTM3NzU3NywiZXhwIjoyMDk2OTUzNTc3fQ.gTaTtNR1NpaNVrCDCOvn9WAEE_KRzGONKZ8VJNymFr0
 *   5. Execute a função setupSheet() uma vez (cria as abas)
 *   6. Execute backupAll() para testar
 *   7. Vá em Triggers (relógio) > Add Trigger:
 *      - backupAll
 *      - Time-driven > Minutes interval > Every 5 minutes
 */

function getHeaders_() {
  var props = PropertiesService.getScriptProperties();
  return {
    'apikey': props.getProperty('SUPABASE_KEY'),
    'Authorization': 'Bearer ' + props.getProperty('SUPABASE_KEY'),
    'Content-Type': 'application/json'
  };
}

function getUrl_() {
  return PropertiesService.getScriptProperties().getProperty('SUPABASE_URL');
}

function fetchTable_(table, params) {
  var sep = (params && params.indexOf('?') >= 0) ? '&' : '?';
  var url = getUrl_() + '/' + table + (params || '') + sep + 'limit=5000';
  var resp = UrlFetchApp.fetch(url, { headers: getHeaders_(), muteHttpExceptions: true });
  if (resp.getResponseCode() === 200) {
    return JSON.parse(resp.getContentText());
  }
  Logger.log('Fetch %s failed: HTTP %d — %s', table, resp.getResponseCode(), resp.getContentText().substring(0, 200));
  return [];
}

function flattenRow_(obj, keys) {
  return keys.map(function(k) {
    var v = obj[k];
    if (v === null || v === undefined) return '';
    if (Array.isArray(v) || typeof v === 'object') return JSON.stringify(v);
    return String(v);
  });
}

function writeSheet_(ws, headers, rows) {
  ws.clear();
  var data = rows.length > 0 ? [headers].concat(rows) : [headers];
  if (data.length > 0) {
    ws.getRange(1, 1, data.length, headers.length).setValues(data);
  }
}

function getOrCreateSheet_(ss, name) {
  var existing = ss.getSheetByName(name);
  if (existing) return existing;
  return ss.insertSheet(name);
}

function setupSheet() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  getOrCreateSheet_(ss, 'Players');
  getOrCreateSheet_(ss, 'Matches');
  getOrCreateSheet_(ss, 'Events');
  SpreadsheetApp.getUi().alert('Abas criadas! Execute backupAll() agora.');
}

function backupAll() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  Logger.log('Fetching data from Supabase...');

  var players = fetchTable_('players', '');
  var matches = fetchTable_('matches', '?order=played_at.desc');
  var events = fetchTable_('events', '?order=date.desc');

  Logger.log('%d players, %d matches, %d events', players.length, matches.length, events.length);

  // Players sheet
  var pKeys = ['id', 'username', 'name', 'tier', 'wins', 'losses', 'kills', 'deaths',
               'region', 'platform', 'affiliation', 'available', 'hidden',
               'previous_tier', 'updated_at'];
  var pRows = players.map(function(p) { return flattenRow_(p, pKeys); });
  var wsP = getOrCreateSheet_(ss, 'Players');
  writeSheet_(wsP, pKeys, pRows);
  Logger.log('Players sheet updated (%d rows)', pRows.length);

  // Matches sheet
  var mKeys = ['id', 'event', 'played_at', 'side1_playerids', 'side2_playerids',
               'side1_score', 'side2_score', 'winning_side', 'status', 'recording_url'];
  var mRows = matches.map(function(m) { return flattenRow_(m, mKeys); });
  var wsM = getOrCreateSheet_(ss, 'Matches');
  writeSheet_(wsM, mKeys, mRows);
  Logger.log('Matches sheet updated (%d rows)', mRows.length);

  // Events sheet
  var eKeys = ['id', 'name', 'date', 'completed', 'completed_at', 'is_tournament', 'description'];
  var eRows = events.map(function(e) { return flattenRow_(e, eKeys); });
  var wsE = getOrCreateSheet_(ss, 'Events');
  writeSheet_(wsE, eKeys, eRows);
  Logger.log('Events sheet updated (%d rows)', eRows.length);

  Logger.log('Backup completo — ' + new Date().toISOString());
}
