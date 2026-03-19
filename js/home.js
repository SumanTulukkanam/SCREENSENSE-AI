// js/home.js
import {
    auth,
    googleProvider,
    signInWithPopup,
    signInWithRedirect,
    getRedirectResult,
    onAuthStateChanged,
    signOut
} from '../config/firebase-config.js';

// ─── Elements ───────────────────────────────────────────────
const authOverlay  = document.getElementById('authOverlay');
const authLoading  = document.getElementById('authLoading');
const googleBtn    = document.getElementById('googleSignInBtn');
const mainPage     = document.getElementById('mainPage');
const navAvatar    = document.getElementById('navAvatar');
const navName      = document.getElementById('navName');
const logoutBtn    = document.getElementById('logoutBtn');
const generateBtn  = document.getElementById('generateBtn');

// ─── 1. Check redirect result (runs on every page load) ────
console.log('🔄 Checking redirect result...');
try {
    const result = await getRedirectResult(auth);
    if (result && result.user) {
        console.log('✅ Came back from Google redirect. User:', result.user.email);
    } else {
        console.log('ℹ️ No redirect result (normal on first load)');
    }
} catch (err) {
    console.warn('⚠️ getRedirectResult error (non-fatal):', err.code, err.message);
}

// ─── 2. Auth state listener ─────────────────────────────────
onAuthStateChanged(auth, (user) => {
    console.log('🔄 onAuthStateChanged fired. User:', user ? user.email : 'null');
    if (user) {
        showMainPage(user);
    } else {
        showAuthOverlay();
    }
});

// ─── 3. Google sign-in button ───────────────────────────────
//      Tries popup first. If blocked → falls back to redirect.
googleBtn.addEventListener('click', async () => {
    authLoading.classList.remove('hidden');
    googleBtn.style.display = 'none';
    console.log('🔄 Sign-in button clicked, trying popup...');

    try {
        const result = await signInWithPopup(auth, googleProvider);
        console.log('✅ Popup sign-in worked:', result.user.email);

    } catch (popupErr) {
        console.warn('⚠️ Popup failed:', popupErr.code, '— trying redirect...');

        try {
            await signInWithRedirect(auth, googleProvider);
        } catch (redirectErr) {
            console.error('❌ Redirect also failed:', redirectErr);
            authLoading.classList.add('hidden');
            googleBtn.style.display = 'flex';
            alert('Sign-in failed. Please try again.');
        }
    }
});

// ─── 4. Logout ──────────────────────────────────────────────
logoutBtn.addEventListener('click', async () => {
    try {
        await signOut(auth);
        sessionStorage.clear();
        localStorage.removeItem('userAuthenticated');
        console.log('✅ Signed out');
    } catch (err) {
        console.error('Sign-out error:', err);
    }
});

// ─── 5. Generate button → go to creator ────────────────────
generateBtn.addEventListener('click', () => {
    window.location.href = 'creator.html';
});

// ─── Show / Hide ────────────────────────────────────────────
function showMainPage(user) {
    authOverlay.classList.add('hidden');
    mainPage.classList.remove('hidden');

    navAvatar.src       = user.photoURL || '';
    navName.textContent = user.displayName || 'User';

    sessionStorage.setItem('currentUserEmail', user.email);
    sessionStorage.setItem('currentUserId',    user.uid);
    localStorage.setItem('userAuthenticated',  'true');
    console.log('✅ Main page shown for:', user.email);
}

function showAuthOverlay() {
    authOverlay.classList.remove('hidden');
    mainPage.classList.add('hidden');
    authLoading.classList.add('hidden');
    googleBtn.style.display = 'flex';
}