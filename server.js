
const express = require('express');
const cors = require('cors');
const path = require('path');

const app = express();
app.use(cors());
app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

app.use('/auth', require('./routes/auth'));
app.use('/upload', require('./routes/upload'));
app.use('/admin', require('./routes/admin'));

app.listen(5000, () => console.log('Server running on port 5000'));
