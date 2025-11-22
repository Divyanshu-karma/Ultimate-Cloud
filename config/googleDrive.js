
const { google } = require('googleapis');
const path = require('path');

const keyFile = path.join(__dirname, '../ups-site-4c4857f485a3.json');

const auth = new google.auth.GoogleAuth({
  keyFile,
  scopes: ['https://www.googleapis.com/auth/drive']
});

const drive = google.drive({ version: 'v3', auth });
module.exports = drive;
