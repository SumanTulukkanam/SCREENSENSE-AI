package com.example.screensenseagent

import android.content.Intent
import android.os.Bundle
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.*
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.screensenseagent.ui.theme.ScreenSenseAgentTheme
import com.google.android.gms.auth.api.signin.GoogleSignIn
import com.google.android.gms.auth.api.signin.GoogleSignInOptions
import com.google.android.gms.common.api.ApiException
import com.google.firebase.auth.FirebaseAuth
import com.google.firebase.auth.GoogleAuthProvider

class AuthActivity : ComponentActivity() {

    private val auth = FirebaseAuth.getInstance()

    // Google Sign-In launcher
    private var googleErrorCallback: ((String) -> Unit)? = null

    private val googleSignInLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        val task = GoogleSignIn.getSignedInAccountFromIntent(result.data)
        try {
            val account = task.getResult(ApiException::class.java)
            firebaseAuthWithGoogle(account.idToken!!)
        } catch (e: ApiException) {
            Log.e("AuthActivity", "Google sign-in failed code: ${e.statusCode} msg: ${e.message}")
            googleErrorCallback?.invoke("Google sign-in failed (code ${e.statusCode}). Check internet.")
        }
    }

    private fun launchGoogleSignIn(onError: (String) -> Unit) {
        googleErrorCallback = onError
        try {
            val gso = GoogleSignInOptions.Builder(GoogleSignInOptions.DEFAULT_SIGN_IN)
                .requestIdToken(getString(R.string.default_web_client_id))
                .requestEmail()
                .build()
            val client = GoogleSignIn.getClient(this, gso)
            googleSignInLauncher.launch(client.signInIntent)
        } catch (e: Exception) {
            onError("Google setup failed: ${e.message}")
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // If already signed in, skip to MainActivity
        if (auth.currentUser != null) {
            goToMain()
            return
        }

        enableEdgeToEdge()
        setContent {
            ScreenSenseAgentTheme {
                AuthScreen(
                    onEmailLogin = { email, password, onError ->
                        signInWithEmail(email, password, onError)
                    },
                    onEmailSignup = { name, email, password, onError ->
                        signUpWithEmail(name, email, password, onError)
                    },
                    onGoogleSignIn = { onError ->
                        launchGoogleSignIn(onError)
                    }
                )
            }
        }
    }

    private fun signInWithEmail(email: String, password: String, onError: (String) -> Unit) {
        auth.signInWithEmailAndPassword(email, password)
            .addOnSuccessListener { goToMain() }
            .addOnFailureListener { e ->
                onError(friendlyError(e.message ?: ""))
            }
    }

    private fun signUpWithEmail(name: String, email: String, password: String, onError: (String) -> Unit) {
        auth.createUserWithEmailAndPassword(email, password)
            .addOnSuccessListener { result ->
                val profileUpdates = com.google.firebase.auth.UserProfileChangeRequest.Builder()
                    .setDisplayName(name).build()
                result.user?.updateProfile(profileUpdates)
                goToMain()
            }
            .addOnFailureListener { e ->
                onError(friendlyError(e.message ?: ""))
            }
    }

    private fun launchGoogleSignIn() {
        val gso = GoogleSignInOptions.Builder(GoogleSignInOptions.DEFAULT_SIGN_IN)
            .requestIdToken(getString(R.string.default_web_client_id))
            .requestEmail()
            .build()
        val client = GoogleSignIn.getClient(this, gso)
        googleSignInLauncher.launch(client.signInIntent)
    }

    private fun firebaseAuthWithGoogle(idToken: String) {
        val credential = GoogleAuthProvider.getCredential(idToken, null)
        auth.signInWithCredential(credential)
            .addOnSuccessListener { goToMain() }
            .addOnFailureListener { e ->
                Log.e("AuthActivity", "Firebase Google auth failed: ${e.message}")
            }
    }

    private fun goToMain() {
        startActivity(Intent(this, MainActivity::class.java))
        finish()
    }

    private fun friendlyError(msg: String): String = when {
        msg.contains("no user record") -> "No account found with this email."
        msg.contains("password is invalid") -> "Incorrect password."
        msg.contains("email address is already") -> "Email already registered. Sign in instead."
        msg.contains("badly formatted") -> "Invalid email address."
        msg.contains("at least 6") -> "Password must be at least 6 characters."
        msg.contains("network") -> "Network error. Check your connection."
        msg.contains("too many") -> "Too many attempts. Try again later."
        else -> "Something went wrong. Please try again."
    }
}

/* ─────────────────────────────────────────
   Composable UI
───────────────────────────────────────── */

val Purple = Color(0xFF6C63FF)
val DarkBg = Color(0xFF0D0D1A)
val CardBg = Color(0xFF13131F)
val BorderColor = Color(0xFF2A2A3F)
val TextMuted = Color(0xFF7070A0)
val GreenAccent = Color(0xFF00E5A0)
val RedAccent = Color(0xFFFF4F6D)

@Composable

fun AuthScreen(
    onEmailLogin: (String, String, (String) -> Unit) -> Unit,
    onEmailSignup: (String, String, String, (String) -> Unit) -> Unit,
    onGoogleSignIn: ((String) -> Unit) -> Unit    // ← changed
) {
    var isLogin by remember { mutableStateOf(true) }
    var isLoading by remember { mutableStateOf(false) }
    var errorMsg by remember { mutableStateOf("") }

    // Login fields
    var loginEmail by remember { mutableStateOf("") }
    var loginPassword by remember { mutableStateOf("") }

    // Signup fields
    var signupName by remember { mutableStateOf("") }
    var signupEmail by remember { mutableStateOf("") }
    var signupPassword by remember { mutableStateOf("") }
    var showPassword by remember { mutableStateOf(false) }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(DarkBg),
        contentAlignment = Alignment.Center
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(24.dp),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {

            // ── Brand header ──
            Text("📱", fontSize = 48.sp)
            Spacer(Modifier.height(8.dp))
            Text(
                "ScreenSense AI",
                fontSize = 26.sp,
                fontWeight = FontWeight.Bold,
                color = Color.White
            )
            Text(
                "// analyse · detect · improve",
                fontSize = 12.sp,
                color = TextMuted,
                modifier = Modifier.padding(bottom = 32.dp)
            )

            // ── Card ──
            Card(
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(20.dp),
                colors = CardDefaults.cardColors(containerColor = CardBg),
                border = androidx.compose.foundation.BorderStroke(1.dp, BorderColor)
            ) {
                Column(modifier = Modifier.padding(24.dp)) {

                    // ── Tab row ──
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .clip(RoundedCornerShape(10.dp))
                            .background(Color(0xFF0D0D1A))
                            .padding(4.dp)
                    ) {
                        listOf("Sign In" to true, "Sign Up" to false).forEach { (label, isLoginTab) ->
                            Box(
                                modifier = Modifier
                                    .weight(1f)
                                    .clip(RoundedCornerShape(8.dp))
                                    .background(if (isLogin == isLoginTab) Purple else Color.Transparent)
                                    .padding(vertical = 10.dp),
                                contentAlignment = Alignment.Center
                            ) {
                                TextButton(
                                    onClick = {
                                        isLogin = isLoginTab
                                        errorMsg = ""
                                    },
                                    modifier = Modifier.fillMaxWidth()
                                ) {
                                    Text(
                                        label,
                                        color = if (isLogin == isLoginTab) Color.White else TextMuted,
                                        fontWeight = FontWeight.SemiBold,
                                        fontSize = 14.sp
                                    )
                                }
                            }
                        }
                    }

                    Spacer(Modifier.height(20.dp))

                    // ── Form fields ──
                    AnimatedContent(targetState = isLogin, label = "form") { login ->
                        Column {
                            if (login) {
                                // LOGIN FORM
                                AuthTextField(
                                    value = loginEmail,
                                    onValueChange = { loginEmail = it },
                                    label = "Email",
                                    placeholder = "you@example.com",
                                    keyboardType = KeyboardType.Email
                                )
                                Spacer(Modifier.height(12.dp))
                                AuthTextField(
                                    value = loginPassword,
                                    onValueChange = { loginPassword = it },
                                    label = "Password",
                                    placeholder = "••••••••",
                                    isPassword = true,
                                    showPassword = showPassword,
                                    onTogglePassword = { showPassword = !showPassword }
                                )
                            } else {
                                // SIGNUP FORM
                                AuthTextField(
                                    value = signupName,
                                    onValueChange = { signupName = it },
                                    label = "Full Name",
                                    placeholder = "Your name"
                                )
                                Spacer(Modifier.height(12.dp))
                                AuthTextField(
                                    value = signupEmail,
                                    onValueChange = { signupEmail = it },
                                    label = "Email",
                                    placeholder = "you@example.com",
                                    keyboardType = KeyboardType.Email
                                )
                                Spacer(Modifier.height(12.dp))
                                AuthTextField(
                                    value = signupPassword,
                                    onValueChange = { signupPassword = it },
                                    label = "Password",
                                    placeholder = "Min. 6 characters",
                                    isPassword = true,
                                    showPassword = showPassword,
                                    onTogglePassword = { showPassword = !showPassword }
                                )
                                // Password strength bar
                                if (signupPassword.isNotEmpty()) {
                                    Spacer(Modifier.height(6.dp))
                                    PasswordStrengthBar(signupPassword)
                                }
                            }
                        }
                    }

                    // ── Error message ──
                    if (errorMsg.isNotEmpty()) {
                        Spacer(Modifier.height(12.dp))
                        Text(
                            errorMsg,
                            color = RedAccent,
                            fontSize = 12.sp,
                            modifier = Modifier
                                .fillMaxWidth()
                                .clip(RoundedCornerShape(8.dp))
                                .background(Color(0x22FF4F6D))
                                .padding(10.dp)
                        )
                    }

                    Spacer(Modifier.height(20.dp))

                    // ── Primary button ──
                    Button(
                        onClick = {
                            errorMsg = ""
                            isLoading = true
                            if (isLogin) {
                                if (loginEmail.isBlank() || loginPassword.isBlank()) {
                                    errorMsg = "Please fill in all fields."
                                    isLoading = false
                                    return@Button
                                }
                                onEmailLogin(loginEmail, loginPassword) { err ->
                                    errorMsg = err
                                    isLoading = false
                                }
                            } else {
                                if (signupName.isBlank() || signupEmail.isBlank() || signupPassword.isBlank()) {
                                    errorMsg = "Please fill in all fields."
                                    isLoading = false
                                    return@Button
                                }
                                onEmailSignup(signupName, signupEmail, signupPassword) { err ->
                                    errorMsg = err
                                    isLoading = false
                                }
                            }
                        },
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(50.dp),
                        shape = RoundedCornerShape(12.dp),
                        colors = ButtonDefaults.buttonColors(containerColor = Purple),
                        enabled = !isLoading
                    ) {
                        if (isLoading) {
                            CircularProgressIndicator(
                                color = Color.White,
                                modifier = Modifier.size(20.dp),
                                strokeWidth = 2.dp
                            )
                        } else {
                            Text(
                                if (isLogin) "Sign In →" else "Create Account →",
                                fontWeight = FontWeight.SemiBold,
                                fontSize = 15.sp
                            )
                        }
                    }

                    Spacer(Modifier.height(16.dp))

                    // ── Divider ──
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Divider(modifier = Modifier.weight(1f), color = BorderColor)
                        Text("  or continue with  ", color = TextMuted, fontSize = 12.sp)
                        Divider(modifier = Modifier.weight(1f), color = BorderColor)
                    }

                    Spacer(Modifier.height(16.dp))

                    // ── Google button ──
                    OutlinedButton(
                        onClick = {
                            errorMsg = ""
                            isLoading = true
                            onGoogleSignIn { err ->          // ← passes error handler
                                errorMsg = err
                                isLoading = false
                            }
                        },
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(50.dp),
                        shape = RoundedCornerShape(12.dp),
                        border = androidx.compose.foundation.BorderStroke(1.dp, BorderColor),
                        colors = ButtonDefaults.outlinedButtonColors(contentColor = Color.White)
                    ) {
                        Text("G", fontWeight = FontWeight.Bold, color = Color(0xFF4285F4), fontSize = 18.sp)
                        Spacer(Modifier.width(8.dp))
                        Text("Continue with Google", fontSize = 14.sp)
                    }
                }
            }
        }
    }
}

@Composable
fun AuthTextField(
    value: String,
    onValueChange: (String) -> Unit,
    label: String,
    placeholder: String,
    keyboardType: KeyboardType = KeyboardType.Text,
    isPassword: Boolean = false,
    showPassword: Boolean = false,
    onTogglePassword: (() -> Unit)? = null
) {
    Column {
        Text(label, color = TextMuted, fontSize = 12.sp, modifier = Modifier.padding(bottom = 6.dp))
        OutlinedTextField(
            value = value,
            onValueChange = onValueChange,
            placeholder = { Text(placeholder, color = TextMuted) },
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(10.dp),
            colors = OutlinedTextFieldDefaults.colors(
                focusedBorderColor = Purple,
                unfocusedBorderColor = BorderColor,
                focusedTextColor = Color.White,
                unfocusedTextColor = Color.White,
                cursorColor = Purple,
                focusedContainerColor = Color(0xFF0D0D1A),
                unfocusedContainerColor = Color(0xFF0D0D1A)
            ),
            keyboardOptions = KeyboardOptions(keyboardType = if (isPassword) KeyboardType.Password else keyboardType),
            visualTransformation = if (isPassword && !showPassword) PasswordVisualTransformation() else VisualTransformation.None,
            trailingIcon = if (isPassword && onTogglePassword != null) {
                { TextButton(onClick = onTogglePassword) { Text(if (showPassword) "Hide" else "Show", color = TextMuted, fontSize = 11.sp) } }
            } else null,
            singleLine = true
        )
    }
}

@Composable
fun PasswordStrengthBar(password: String) {
    var strength = 0
    if (password.length >= 6) strength++
    if (password.length >= 10) strength++
    if (password.any { it.isUpperCase() } && password.any { it.isDigit() }) strength++
    if (password.any { !it.isLetterOrDigit() }) strength++

    val color = when (strength) {
        1 -> RedAccent
        2 -> Color(0xFFFFB347)
        3 -> Purple
        4 -> GreenAccent
        else -> BorderColor
    }
    val label = when (strength) {
        1 -> "Weak"
        2 -> "Fair"
        3 -> "Good"
        4 -> "Strong 💪"
        else -> ""
    }

    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(4.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        repeat(4) { i ->
            Box(
                modifier = Modifier
                    .weight(1f)
                    .height(4.dp)
                    .clip(RoundedCornerShape(2.dp))
                    .background(if (i < strength) color else BorderColor)
            )
        }
        Spacer(Modifier.width(8.dp))
        Text(label, color = color, fontSize = 11.sp)
    }
}