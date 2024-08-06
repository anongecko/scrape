from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from flask_login import login_user, logout_user, current_user  
from werkzeug.security import check_password_hash

from app.models import User  
{
  "name": "your-project-name", // Replace with your project's name
  "version": "0.1.0",
  "private": true,  // Optional: Prevents accidental publishing to npm
  "scripts": {
    "dev": "next dev",       // Start development server
    "build": "next build",    // Build for production
    "start": "next start",    // Start production server
    "lint": "next lint"       // Run linter (if you have it set up)
  },
  "dependencies": {
    "next": "13.4.19",      // Latest Next.js version (adjust if needed)
    "react": "18.2.0",        // React version (should match Next.js)
    "react-dom": "18.2.0",    // React DOM version (should match Next.js)
    "react-hook-form": "7.45.4"  // Add react-hook-form dependency
  }
}

