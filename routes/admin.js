
const express = require('express');
const fs = require('fs');
const drive = require('../config/googleDrive');
const jwt = require('jsonwebtoken');
const router = express.Router();
const userFile = './data/user.json';
const SECRET = 'supersecretkey';

const ADMIN_USER = 'admin';
const ADMIN_PASS = 'admin123';

router.post('/login', (req, res) => {
  const { username, password } = req.body;
  if (username !== ADMIN_USER || password !== ADMIN_PASS) return res.status(403).send('Invalid admin');
  const token = jwt.sign({ username: 'admin', role: 'admin' }, SECRET, { expiresIn: '1d' });
  res.json({ token });
});

function adminAuth(req, res, next) {
  const token = req.headers['authorization'];
  if (!token) return res.status(403).send('No token');
  try {
    const decoded = jwt.verify(token, SECRET);
    if (decoded.role !== 'admin') return res.status(403).send('Not admin');
    next();
  } catch {
    res.status(401).send('Invalid token');
  }
}

router.get('/users', adminAuth, (req, res) => {
  const users = JSON.parse(fs.readFileSync(userFile));
  res.json(users);
});

router.delete('/user/:username', adminAuth, async (req, res) => {
  const username = req.params.username;
  let users = JSON.parse(fs.readFileSync(userFile));
  users = users.filter(u => u.username !== username);
  fs.writeFileSync(userFile, JSON.stringify(users));
  const folderRes = await drive.files.list({ q: `name='${username}' and mimeType='application/vnd.google-apps.folder'` });
  if (folderRes.data.files.length > 0) {
    await drive.files.delete({ fileId: folderRes.data.files[0].id });
  }
  res.send('User deleted');
});

module.exports = router;
