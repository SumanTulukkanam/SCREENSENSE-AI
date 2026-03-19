// config/firebase-config.js

import { initializeApp } from 'https://www.gstatic.com/firebasejs/9.22.0/firebase-app.js';
import { 
    getAuth, 
    GoogleAuthProvider, 
    signInWithPopup,
    signInWithRedirect,
    getRedirectResult,
    onAuthStateChanged, 
    signOut 
} from 'https://www.gstatic.com/firebasejs/9.22.0/firebase-auth.js';
import { 
    getFirestore, 
    doc, 
    setDoc, 
    getDoc, 
    updateDoc,
    deleteDoc
} from 'https://www.gstatic.com/firebasejs/9.22.0/firebase-firestore.js';

// For Firebase JS SDK v7.20.0 and later, measurementId is optional
const firebaseConfig = {
  apiKey: "AIzaSyCP0qBf4blmazVpaIygXG5w-E9l6sY2N1Y",
  authDomain: "screensense-ai-f8fd9.firebaseapp.com",
  projectId: "screensense-ai-f8fd9",
  storageBucket: "screensense-ai-f8fd9.firebasestorage.app",
  messagingSenderId: "788961774235",
  appId: "1:788961774235:web:31156f26fc26d92bbd543e",
  measurementId: "G-ER1T1Q5NZ9"
};

const app = initializeApp(firebaseConfig);
const auth = getAuth(app);
const db = getFirestore(app);
const googleProvider = new GoogleAuthProvider();

// Global availability
window.auth = auth;
window.db = db;
window.googleProvider = googleProvider;
window.signInWithPopup = signInWithPopup;
window.signInWithRedirect = signInWithRedirect;
window.getRedirectResult = getRedirectResult;
window.onAuthStateChanged = onAuthStateChanged;
window.signOut = signOut;
window.doc = doc;
window.setDoc = setDoc;
window.getDoc = getDoc;
window.updateDoc = updateDoc;
window.deleteDoc = deleteDoc;

console.log('✅ Firebase initialized successfully');
console.log('✅ Auth available:', !!window.auth);
console.log('✅ Firestore available:', !!window.db);

export { 
    auth, 
    db, 
    googleProvider, 
    signInWithPopup,
    signInWithRedirect,
    getRedirectResult,
    onAuthStateChanged, 
    signOut,
    doc, 
    setDoc, 
    getDoc, 
    updateDoc,
    deleteDoc 
};