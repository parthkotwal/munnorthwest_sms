#!/bin/bash
echo "Installing Node dependencies..."
npm install

echo "Building Tailwind CSS..."
npm run build:css

echo "Tailwind CSS build complete."