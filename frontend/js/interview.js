let interviewData = {
    candidateId: "",
    sessionId: "",
    questions: [],
    currentQuestionIndex: 0,
    currentQuestion: null,
    answers: [],
    cameraStream: null,
    isInterviewActive: false,
    evaluationInterval: null,
    questionStartedAt: 0,
    liveTranscript: "",
    totalQuestions: 0,
    lastLiveSignals: null,
    lastProctorSignals: null,
};

let questionTimer = null;
let speechRecognition = null;
let voiceActive = false;
let proctorInterval = null;
let proctorBusy = false;
let lastProctorAlertAt = 0;
let lastProctorAlertKey = "";
let lastTranscriptTickAt = 0;
let transcriptWordCount = 0;
let transcriptStats = { wpm: 0, filler_count: 0, silence_seconds: 0 };
let timelineItems = [];

function escapeHTML(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        "\"": "&quot;",
        "'": "&#039;",
    })[char]);
}

document.addEventListener("DOMContentLoaded", function () {
    updateUI();
    bootstrapInterviewHealth();
    pushTimeline("ready", "Interview console ready.", "info");
});

function pushTimeline(kind, message, severity = "info") {
    const item = {
        ts: new Date().toISOString(),
        kind: String(kind || "event"),
        message: String(message || "").trim(),
        severity: String(severity || "info"),
    };
    timelineItems = [item, ...timelineItems].slice(0, 30);
    renderTimeline();
}

function renderTimeline() {
    const container = document.getElementById("interviewTimeline");
    if (!container) return;

    container.innerHTML = timelineItems.length ? timelineItems.map(item => `
        <div class="mini-row" style="border-bottom:1px solid rgba(148,163,184,0.10);">
            <span class="muted">${escapeHTML(item.kind)} · ${escapeHTML(new Date(item.ts).toLocaleTimeString())}</span>
            <strong class="tone-${escapeHTML(item.severity === "success" ? "good" : item.severity === "error" ? "bad" : item.severity === "warn" ? "warn" : "muted")}">${escapeHTML(item.message)}</strong>
        </div>
    `).join("") : `<div class="muted">Timeline will populate during the interview.</div>`;
}

function pushNote(text) {
    const ul = document.getElementById("aiNotesList");
    if (!ul) return;
    const msg = String(text || "").trim();
    if (!msg) return;
    // Avoid spamming duplicates.
    const first = ul.firstChild?.textContent || "";
    if (first.trim() === msg) return;

    const li = document.createElement("li");
    li.textContent = msg;
    ul.prepend(li);

    while (ul.children.length > 10) {
        ul.removeChild(ul.lastChild);
    }
}

function getSpeechRecognition() {
    return window.SpeechRecognition || window.webkitSpeechRecognition || null;
}

function renderQuestion(question, progress = 1, total = interviewData.totalQuestions || 1) {
    interviewData.currentQuestion = question || null;
    const container = document.getElementById("questionContainer");

    if (!question) {
        container.innerHTML = `
            <div class="question-card">
                <div class="question-text">Interview complete. Review the final evaluation on the right.</div>
            </div>
        `;
        return;
    }

    container.innerHTML = `
        <div class="question-card">
            <div class="question-header">
                <span class="question-type">${question.type || "general"}</span>
                <span class="question-timer" id="questionTimer">3:00</span>
            </div>
            <div class="question-text">${question.question}</div>
        </div>
    `;

    interviewData.currentQuestionIndex = Math.max(0, progress - 1);
    interviewData.totalQuestions = total || interviewData.totalQuestions;
    updateProgress();
}

async function startInterview() {
    const candidateId = document.getElementById("candidateId").value.trim();
    const experienceLevel = document.getElementById("experienceLevel").value;
    const interviewType = document.getElementById("interviewType").value;
    const jobDescription = document.getElementById("jobDescription").value.trim();

    if (!candidateId) {
        showAlert("Please enter a candidate ID", "error");
        return;
    }

    try {
        showLoader("Generating interview questions...");
        const response = await apiInterviewStart({
            candidate_id: candidateId,
            experience_level: experienceLevel,
            interview_type: interviewType,
            job_description: jobDescription,
            candidate_skills: [],
            resume_text: "",
        });

        interviewData.candidateId = candidateId;
        interviewData.sessionId = response.session_id;
        interviewData.questions = response.question ? [response.question] : [];
        interviewData.answers = [];
        interviewData.isInterviewActive = true;
        interviewData.totalQuestions = response.total || 1;
        interviewData.currentQuestionIndex = Math.max(0, (response.progress || 1) - 1);

        document.getElementById("setupSection").classList.add("hidden");
        document.getElementById("interviewInterface").classList.remove("hidden");
        document.getElementById("startInterviewBtn").disabled = true;
        document.getElementById("endInterviewBtn").disabled = false;
        document.getElementById("submitAnswerBtn").disabled = false;
        document.getElementById("answerInput").disabled = false;

        renderQuestion(response.question, response.progress, response.total);
        updateLiveTranscript("");
        startRealtimeEvaluation();
        startQuestionTimer();
        startProctoringLoop();
        hideLoader();
        showAlert("Interview started successfully!", "success");
        pushTimeline("start", `Interview started for candidate ${candidateId}.`, "success");
    } catch (error) {
        hideLoader();
        showAlert("Failed to start interview: " + error.message, "error");
        pushTimeline("error", `Start failed: ${error.message || "unknown error"}`, "error");
    }
}

function startQuestionTimer() {
    if (questionTimer) {
        clearInterval(questionTimer);
    }

    interviewData.questionStartedAt = Date.now();
    let timeLeft = 180;
    questionTimer = setInterval(() => {
        const timer = document.getElementById("questionTimer");
        if (!timer) {
            clearInterval(questionTimer);
            return;
        }

        timeLeft -= 1;
        const minutes = Math.floor(timeLeft / 60);
        const seconds = timeLeft % 60;
        timer.textContent = `${minutes}:${seconds.toString().padStart(2, "0")}`;

        if (timeLeft <= 0) {
            clearInterval(questionTimer);
            submitAnswer();
        }
    }, 1000);
}

async function submitAnswer() {
    const answer = document.getElementById("answerInput").value.trim();
    if (!answer || !interviewData.currentQuestion) {
        showAlert("Please provide an answer before submitting.", "error");
        return;
    }

    const timeTaken = interviewData.questionStartedAt ? (Date.now() - interviewData.questionStartedAt) / 1000 : 0;
    if (questionTimer) {
        clearInterval(questionTimer);
        questionTimer = null;
    }

    stopRealtimeEvaluation();
    stopVoiceInput();
    stopProctoringLoop();

    try {
        showLoader("Evaluating answer...");
        const response = await apiInterviewAnswer({
            session_id: interviewData.sessionId,
            candidate_id: interviewData.candidateId,
            answer,
            transcript: interviewData.liveTranscript || answer,
            time_taken: timeTaken,
        });

        interviewData.answers.push({
            question_id: interviewData.currentQuestion.id,
            question_text: interviewData.currentQuestion.question,
            answer,
            time_taken: timeTaken,
            evaluation: {
                score: response.score,
                feedback: response.feedback,
                confidence: response.confidence,
            },
        });

        updateLiveMetrics(response);
        if (response.feedback) {
            pushNote(`Answer feedback: ${response.feedback}`);
        }
        document.getElementById("answerInput").value = "";
        updateLiveTranscript("");
        hideLoader();

        if (response.completed) {
            await endInterview(false);
            if (response.result) {
                displayFinalEvaluation(response.result);
            }
            showAlert("Interview completed successfully!", "success");
            pushTimeline("complete", "Interview completed.", "success");
            return;
        }

        renderQuestion(response.next_question, response.progress, response.total);
        startRealtimeEvaluation();
        startQuestionTimer();
        startProctoringLoop();
        showAlert(response.feedback || "Answer evaluated", "success");
        pushTimeline("answer", "Answer submitted and evaluated.", "info");
    } catch (error) {
        hideLoader();
        showAlert("Failed to evaluate answer: " + error.message, "error");
        pushTimeline("error", `Evaluation failed: ${error.message || "unknown error"}`, "error");
    }
}

function startRealtimeEvaluation() {
    stopRealtimeEvaluation();
    interviewData.evaluationInterval = setInterval(() => {
        const answer = document.getElementById("answerInput").value.trim();
        if (answer) {
            refreshLiveScore(answer);
        }
    }, 2500);
}

function stopRealtimeEvaluation() {
    if (interviewData.evaluationInterval) {
        clearInterval(interviewData.evaluationInterval);
        interviewData.evaluationInterval = null;
    }
}

async function refreshLiveScore(answer) {
    if (!interviewData.currentQuestion || !interviewData.isInterviewActive) {
        return;
    }

    try {
        const response = await apiInterviewRealtimeScore({
            question: interviewData.currentQuestion.question,
            answer,
            time_taken: interviewData.questionStartedAt ? (Date.now() - interviewData.questionStartedAt) / 1000 : 0,
            question_type: interviewData.currentQuestion.type || "technical",
        });
        updateLiveMetrics(response);
    } catch (error) {
        console.warn("live_scoring_recovered", error);
    }
}

function updateLiveMetrics(response) {
    interviewData.lastLiveSignals = response || null;
    const evaluation = response.evaluation || {};
    document.getElementById("responseScore").textContent = evaluation.score ?? "-";
    document.getElementById("timeEfficiency").textContent = evaluation.time_efficiency || "-";
    document.getElementById("questionType").textContent = interviewData.currentQuestion?.type || "-";
    document.getElementById("liveScore").textContent = response.live_score != null ? response.live_score : "-";
    document.getElementById("liveConfidence").textContent = response.confidence_level || "-";
    renderCombinedSignals();

    const followups = Array.isArray(response.follow_up_suggestions) ? response.follow_up_suggestions : [];
    if (followups.length) {
        pushNote(`Follow-up probe: ${followups[0]}`);
    }
}

async function endInterview(showAlertMessage = true) {
    interviewData.isInterviewActive = false;
    stopRealtimeEvaluation();
    stopVoiceInput();
    stopCamera();

    if (questionTimer) {
        clearInterval(questionTimer);
        questionTimer = null;
    }

    document.getElementById("startInterviewBtn").disabled = false;
    document.getElementById("endInterviewBtn").disabled = true;
    document.getElementById("submitAnswerBtn").disabled = true;
    document.getElementById("answerInput").disabled = true;

    if (interviewData.sessionId && interviewData.answers.length > 0) {
        await displayFinalResult();
    }

    if (showAlertMessage) {
        showAlert("Interview ended. Final evaluation is ready to generate.", "success");
    }
}

async function displayFinalResult() {
    if (!interviewData.sessionId) {
        showAlert("No interview session found.", "error");
        return;
    }

    try {
        showLoader("Generating final evaluation...");
        const response = await apiInterviewResult(interviewData.sessionId);
        displayFinalEvaluation(response);
        if (response?.recruiter_report?.headline) {
            pushNote(response.recruiter_report.headline);
        }
        hideLoader();
        showAlert("Evaluation generated successfully!", "success");
        pushTimeline("report", "Final interview report generated.", "success");
    } catch (error) {
        hideLoader();
        showAlert("Failed to generate final evaluation: " + error.message, "error");
        pushTimeline("report", "Final report generation failed.", "error");
    }
}

function displayFinalEvaluation(evaluation) {
    document.getElementById("evaluationSection").classList.remove("hidden");
    document.getElementById("finalScore").textContent = evaluation.overall_score?.toFixed ? evaluation.overall_score.toFixed(1) : evaluation.overall_score;

    const breakdown = evaluation.breakdown || {};
    document.getElementById("technicalScore").textContent = evaluation.technical_score ?? breakdown.technical ?? "-";
    document.getElementById("behavioralScore").textContent = breakdown.behavioral ?? "-";
    document.getElementById("communicationScore").textContent = evaluation.communication_score ?? breakdown.communication ?? "-";
    document.getElementById("confidenceScore").textContent = breakdown.problem_solving ?? "-";

    const recommendation = document.getElementById("recommendation");
    recommendation.textContent = evaluation.recommendation || "consider";
    recommendation.className = "recommendation";
    recommendation.classList.add((evaluation.recommendation || "consider").toLowerCase());

    const insightsList = document.getElementById("insightsList");
    insightsList.innerHTML = "";
    const insights = [
        ...(evaluation.strengths || []).map(item => `Strength: ${item}`),
        ...(evaluation.weaknesses || []).map(item => `Weakness: ${item}`),
        ...(evaluation.insights || []),
    ];
    const report = evaluation.recruiter_report || null;
    if (report && typeof report === "object") {
        if (report.communication_summary) insights.push(`Communication: ${report.communication_summary}`);
        if (report.technical_depth_summary) insights.push(`Technical depth: ${report.technical_depth_summary}`);
        if (report.next_step_recommendation) insights.push(`Next step: ${report.next_step_recommendation}`);
        if (Array.isArray(report.concerns) && report.concerns.length) {
            report.concerns.slice(0, 2).forEach(item => insights.push(`Concern: ${item}`));
        }
        if (Array.isArray(report.strengths) && report.strengths.length) {
            report.strengths.slice(0, 2).forEach(item => insights.push(`Signal: ${item}`));
        }
    }
    insights.forEach(insight => {
        const li = document.createElement("li");
        li.textContent = insight;
        insightsList.appendChild(li);
    });

    if (report?.headline) {
        pushNote(report.headline);
    }
}

async function startCamera() {
    try {
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            throw new Error("Camera access is not supported in this browser.");
        }
        const stream = await navigator.mediaDevices.getUserMedia({
            video: { width: 640, height: 480 },
            audio: false,
        });

        interviewData.cameraStream = stream;
        const video = document.getElementById("interviewVideo");
        video.srcObject = stream;
        video.style.display = "block";
        document.getElementById("videoPlaceholder").style.display = "none";

        document.getElementById("startCameraBtn").disabled = true;
        document.getElementById("stopCameraBtn").disabled = false;
        document.getElementById("cameraStatus").className = "status-indicator active";
        document.getElementById("cameraStatus").innerHTML = "<span>&bull;</span> Camera On";

        startRealtimeEvaluation();
        // If the interview is active, resume proctoring immediately when camera comes online.
        if (interviewData.isInterviewActive) {
            startProctoringLoop();
        }
        showAlert("Camera started successfully", "success");
        pushTimeline("camera", "Camera connected.", "success");
    } catch (error) {
        const msg = String(error?.message || error || "");
        const lower = msg.toLowerCase();
        if (lower.includes("permission") || lower.includes("denied")) {
            showAlert("Camera permission is blocked. Allow camera access in your browser settings, then try again.", "error");
        } else if (lower.includes("notfound") || lower.includes("device")) {
            showAlert("No webcam was detected. Connect a camera and try again.", "error");
        } else {
            showAlert("Failed to start camera: " + msg, "error");
        }
        document.getElementById("cameraStatus").className = "status-indicator error";
        document.getElementById("cameraStatus").innerHTML = "<span>&bull;</span> Camera Error";
        pushTimeline("camera", "Camera failed to start (permissions or device issue).", "error");
    }
}

function stopCamera() {
    if (interviewData.cameraStream) {
        interviewData.cameraStream.getTracks().forEach(track => track.stop());
        interviewData.cameraStream = null;
    }

    const video = document.getElementById("interviewVideo");
    video.style.display = "none";
    video.srcObject = null;
    document.getElementById("videoPlaceholder").style.display = "flex";
    document.getElementById("startCameraBtn").disabled = false;
    document.getElementById("stopCameraBtn").disabled = true;
    document.getElementById("cameraStatus").className = "status-indicator inactive";
    document.getElementById("cameraStatus").innerHTML = "<span>&bull;</span> Camera Off";
    stopProctoringLoop();
    pushTimeline("camera", "Camera stopped.", "info");
}

async function captureFaceFrame() {
    const video = document.getElementById("interviewVideo");
    if (!video || !video.videoWidth) {
        throw new Error("Start the camera before verifying face.");
    }

    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL("image/png");
}

async function verifyFace() {
    try {
        const imageBase64 = await captureFaceFrame();
        const response = await apiFaceVerify({
            image_base64: imageBase64,
            candidate_id: interviewData.candidateId || document.getElementById("candidateId").value.trim(),
        });

        document.getElementById("cameraStatus").className = response.verified
            ? "status-indicator active"
            : "status-indicator error";
        document.getElementById("cameraStatus").innerHTML = response.verified
            ? "<span>&bull;</span> Face Verified"
            : "<span>&bull;</span> Face Not Verified";
        showAlert(response.message || "Face verification complete", response.verified ? "success" : "error");
        pushTimeline("face", response.verified ? "Face verified." : "Face not verified.", response.verified ? "success" : "warn");
    } catch (error) {
        showAlert("Face verification failed: " + error.message, "error");
        pushTimeline("face", "Face verification failed.", "error");
    }
}

async function bootstrapInterviewHealth() {
    // Best-effort: don't block UI if health endpoint is unreachable.
    try {
        const data = await apiCall("/interview/health");
        const vision = data?.vision || {};
        if (!vision?.opencv_available) {
            showAlert("Live video analysis is not enabled on this server yet. You can continue the interview normally; face/behavior signals will be limited until vision is turned on.", "info");
        }
    } catch {
        // Ignore - not fatal for the interview flow.
    }
}

function stopProctoringLoop() {
    if (proctorInterval) {
        clearInterval(proctorInterval);
        proctorInterval = null;
    }
}

function startProctoringLoop() {
    stopProctoringLoop();
    if (!interviewData.isInterviewActive) return;
    if (!interviewData.sessionId) return;
    if (!interviewData.candidateId) return;
    if (!interviewData.cameraStream) return;

    // Capture a frame every ~2s (cheap enough, feels realtime, doesn't melt CPUs).
    proctorInterval = setInterval(async () => {
        try {
            if (proctorBusy) return;
            if (!interviewData.isInterviewActive || !interviewData.cameraStream) return;
            proctorBusy = true;
            const imageBase64 = await captureFaceFrame();
            const result = await apiInterviewProctor({
                session_id: interviewData.sessionId,
                candidate_id: interviewData.candidateId,
                image_base64: imageBase64,
            });
            updateProctorSignals(result || {});
        } catch (err) {
            // Don't spam alerts; just keep the system resilient.
            console.warn("proctoring_error", err);
        } finally {
            proctorBusy = false;
        }
    }, 2000);
}

function updateProctorSignals(result) {
    interviewData.lastProctorSignals = result || null;
    const attention = Number(result.attention_score ?? 0) || 0;
    const visibility = Number(result.visibility_score ?? 0) || 0;
    const engagement = Number(result.engagement_score ?? 0) || 0;
    const integrity = Number(result.integrity_score ?? 0) || 0;

    const attentionNode = document.getElementById("attentionScore");
    if (attentionNode) attentionNode.textContent = attention ? Math.round(attention) : "-";

    // Surface alerts in a calm, recruiter-friendly way.
    const alerts = Array.isArray(result.alerts) ? result.alerts : [];
    const container = document.getElementById("alertsContainer");
    if (container) {
        const keyAlerts = alerts.filter(a => ["multiple_faces", "no_face", "low_light", "blurry_frame"].includes(String(a)));
        if (keyAlerts.length) {
            const now = Date.now();
            const msgKey = keyAlerts.slice(0, 2).join("|");
            if ((now - lastProctorAlertAt) > 9000 || msgKey !== lastProctorAlertKey) {
                lastProctorAlertAt = now;
                lastProctorAlertKey = msgKey;
                showAlert(`Proctoring signal: ${keyAlerts.slice(0, 2).join(", ").replaceAll("_", " ")}`, "error");
                pushTimeline("proctor", `Signal: ${keyAlerts.slice(0, 2).join(", ").replaceAll("_", " ")}`, "warn");
            }
        }
    }

    renderCombinedSignals();
}

function renderCombinedSignals() {
    const signals = document.getElementById("liveSignals");
    if (!signals) return;

    const live = interviewData.lastLiveSignals || {};
    const proctor = interviewData.lastProctorSignals || {};

    const goodSignals = Array.isArray(live.good_signals) ? live.good_signals : [];
    const riskSignals = Array.isArray(live.risk_signals) ? live.risk_signals : [];
    const keywords = Array.isArray(live.keywords_detected) ? live.keywords_detected : [];
    const followups = Array.isArray(live.follow_up_suggestions) ? live.follow_up_suggestions : [];

    const proctorAlerts = Array.isArray(proctor.alerts) ? proctor.alerts : [];
    const visibility = Number(proctor.visibility_score ?? 0) || 0;
    const engagement = Number(proctor.engagement_score ?? 0) || 0;
    const integrity = Number(proctor.integrity_score ?? 0) || 0;

    const commLine = transcriptStats.wpm
        ? `Speech: ${transcriptStats.wpm} wpm · fillers: ${transcriptStats.filler_count}`
        : "Speech: waiting for transcript";

    const proctorLine = proctor && Object.keys(proctor).length
        ? `Vision: vis ${Math.round(visibility)}% · engage ${Math.round(engagement)}% · integrity ${Math.round(integrity)}%`
        : "Vision: start camera to enable face/behavior signals";

    const alertLine = proctorAlerts.length ? `Proctor alerts: ${proctorAlerts.slice(0, 3).join(", ")}` : "";

    signals.innerHTML = `
        <div class="signal-good">${escapeHTML(goodSignals.length ? goodSignals.slice(0, 2).join(" | ") : proctorLine)}</div>
        <div class="signal-risk">${escapeHTML(riskSignals.length ? riskSignals.slice(0, 2).join(" | ") : (alertLine || commLine))}</div>
        <div>${escapeHTML(keywords.length ? `Keywords: ${keywords.join(", ")}` : commLine)}</div>
        ${followups.length ? `<div class="muted" style="margin-top:8px;">Next probe: ${escapeHTML(followups[0])}</div>` : ""}
    `;
}

function startVoiceInput() {
    const Recognition = getSpeechRecognition();
    if (!Recognition) {
        showAlert("Speech recognition is not supported in this browser.", "error");
        return;
    }

    if (!speechRecognition) {
        speechRecognition = new Recognition();
        speechRecognition.continuous = true;
        speechRecognition.interimResults = true;
        speechRecognition.lang = "en-US";

        speechRecognition.onresult = event => {
            let transcript = "";
            for (let index = 0; index < event.results.length; index += 1) {
                transcript += event.results[index][0].transcript + " ";
            }
            transcript = transcript.trim();
            document.getElementById("answerInput").value = transcript;
            updateLiveTranscript(transcript);
            refreshLiveScore(transcript);
            updateSpeechStats(transcript);
        };

        speechRecognition.onerror = event => {
            voiceActive = false;
            document.getElementById("startVoiceBtn").disabled = false;
            document.getElementById("stopVoiceBtn").disabled = true;
            showAlert("Voice input error: " + event.error, "error");
        };

        speechRecognition.onend = () => {
            if (!voiceActive) {
                document.getElementById("startVoiceBtn").disabled = false;
                document.getElementById("stopVoiceBtn").disabled = true;
            }
        };
    }

    voiceActive = true;
    document.getElementById("startVoiceBtn").disabled = true;
    document.getElementById("stopVoiceBtn").disabled = false;
    speechRecognition.start();
}

function updateSpeechStats(transcript) {
    const text = String(transcript || "").trim();
    const now = Date.now();
    const words = text ? text.split(/\s+/g).filter(Boolean) : [];

    // Lightweight WPM estimate (based on incremental word growth).
    if (!lastTranscriptTickAt) {
        lastTranscriptTickAt = now;
        transcriptWordCount = words.length;
    } else {
        const dtSec = Math.max(0.25, (now - lastTranscriptTickAt) / 1000);
        const deltaWords = Math.max(0, words.length - transcriptWordCount);
        const wps = deltaWords / dtSec;
        transcriptStats.wpm = Math.round(Math.min(240, Math.max(0, wps * 60)));
        lastTranscriptTickAt = now;
        transcriptWordCount = words.length;
    }

    const fillers = ["um", "uh", "like", "you know", "actually", "basically", "so"];
    const lower = text.toLowerCase();
    let fillerCount = 0;
    for (const f of fillers) {
        if (f.includes(" ")) {
            const re = new RegExp(`\\b${f.replace(" ", "\\\\s+")}\\\\b`, "g");
            fillerCount += (lower.match(re) || []).length;
        } else {
            const re = new RegExp(`\\b${f}\\\\b`, "g");
            fillerCount += (lower.match(re) || []).length;
        }
    }
    transcriptStats.filler_count = fillerCount;

    // Surface quick communication signals into the existing metric slots when possible.
    const confidenceNode = document.getElementById("liveConfidence");
    if (confidenceNode) {
        const label = transcriptStats.wpm >= 90 && transcriptStats.wpm <= 170 ? "steady" : transcriptStats.wpm > 170 ? "fast" : "slow";
        confidenceNode.textContent = `${label} · ${transcriptStats.wpm || 0} wpm`;
    }
}

function stopVoiceInput() {
    voiceActive = false;
    if (speechRecognition) {
        speechRecognition.stop();
    }
    document.getElementById("startVoiceBtn").disabled = false;
    document.getElementById("stopVoiceBtn").disabled = true;
}

function updateLiveTranscript(text) {
    interviewData.liveTranscript = text || "";
    const transcript = document.getElementById("liveTranscript");
    if (transcript) {
        transcript.textContent = interviewData.liveTranscript || "Live transcript will appear here.";
    }
}

function updateProgress() {
    const current = interviewData.currentQuestionIndex + 1;
    const total = interviewData.totalQuestions || 0;

    document.getElementById("currentQuestion").textContent = total ? current : 0;
    document.getElementById("totalQuestions").textContent = total;

    const progress = total > 0 ? (current / total) * 100 : 0;
    document.getElementById("progressFill").style.width = `${progress}%`;
}

function updateUI() {
    updateProgress();
}

function showAlert(message, type = "info") {
    const alertsContainer = document.getElementById("alertsContainer");
    const alertDiv = document.createElement("div");
    alertDiv.className = "alert-item";
    alertDiv.textContent = message;

    if (type === "success") {
        alertDiv.style.background = "rgba(34, 197, 94, 0.1)";
        alertDiv.style.borderColor = "rgba(34, 197, 94, 0.3)";
        alertDiv.style.color = "#4ADE80";
    } else if (type === "error") {
        alertDiv.style.background = "rgba(239, 68, 68, 0.1)";
        alertDiv.style.borderColor = "rgba(239, 68, 68, 0.3)";
        alertDiv.style.color = "#F87171";
    } else if (type === "info") {
        alertDiv.style.background = "rgba(59, 130, 246, 0.10)";
        alertDiv.style.borderColor = "rgba(59, 130, 246, 0.25)";
        alertDiv.style.color = "#93C5FD";
    }

    alertsContainer.appendChild(alertDiv);
    setTimeout(() => {
        if (alertDiv.parentNode) {
            alertDiv.remove();
        }
    }, 5000);
}

function showLoader(message = "Processing...") {
    const loader = document.getElementById("globalLoader");
    const loaderCard = loader.querySelector(".loader-card");
    loaderCard.textContent = message;
    loader.style.display = "flex";
}

function hideLoader() {
    document.getElementById("globalLoader").style.display = "none";
}

window.addEventListener("beforeunload", function () {
    if (interviewData.isInterviewActive) {
        endInterview(false);
    }
    stopVoiceInput();
    stopCamera();
});
