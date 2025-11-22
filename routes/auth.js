
const express = require('express');
const fs = require('fs');
const bcrypt = require('bcrypt');
const jwt = require('jsonwebtoken');
const router = express.Router();
const userFile = './data/user.json';
const SECRET = 'supersecretkey';

router.post('/register', async (req, res) => {
  const { username, password } = req.body;
  const users = JSON.parse(fs.readFileSync(userFile));
  if (users.find(u => u.username === username)) return res.status(400).send('User exists');
  const hash = await bcrypt.hash(password, 10);
  users.push({ username, password: hash });
  fs.writeFileSync(userFile, JSON.stringify(users));
  res.send('Registered');
});

router.post('/login', async (req, res) => {
  const { username, password } = req.body;
  const users = JSON.parse(fs.readFileSync(userFile));
  const user = users.find(u => u.username === username);
  if (!user) return res.status(400).send('Invalid user');
  const match = await bcrypt.compare(password, user.password);
  if (!match) return res.status(400).send('Invalid password');
  const token = jwt.sign({ username }, SECRET, { expiresIn: '1d' });
  res.json({ token });
});

module.exports = router;
