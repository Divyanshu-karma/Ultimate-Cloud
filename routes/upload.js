
const express = require('express');
const multer = require('multer');
const fs = require('fs');
const drive = require('../config/googleDrive');
const authMiddleware = require('../middleware/authMiddleware');
const router = express.Router();

const upload = multer({ dest: 'uploads/' });

router.post('/photo', authMiddleware, upload.single('photo'), async (req, res) => {
  const username = req.user.username;
  const filePath = req.file.path;

  let folderId = await getOrCreateFolder(username);
  const fileMetadata = { name: req.file.originalname, parents: [folderId] };
  const media = { mimeType: req.file.mimetype, body: fs.createReadStream(filePath) };
  await drive.files.create({ resource: fileMetadata, media, fields: 'id' });

  fs.unlinkSync(filePath);
  res.send('Uploaded successfully');
});

async function getOrCreateFolder(username) {
  const res = await drive.files.list({ q: `name='${username}' and mimeType='application/vnd.google-apps.folder' and trashed=false` });
  if (res.data.files.length > 0) return res.data.files[0].id;
  const folder = await drive.files.create({ resource: { name: username, mimeType: 'application/vnd.google-apps.folder' }, fields: 'id' });
  return folder.data.id;
}

module.exports = router;
