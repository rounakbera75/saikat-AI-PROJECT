document.addEventListener('DOMContentLoaded', () => {
    // --- Elements ---
    const chatHistory = document.getElementById('chat-history');
    const userInput = document.getElementById('user-input');
    const sendBtn = document.getElementById('send-btn');
    const pdfUpload = document.getElementById('pdf-upload');
    const pills = document.querySelectorAll('.pill');
    const charCount = document.getElementById('char-count');
    const downloadPdfBtn = document.getElementById('download-pdf-btn');
    const loadingOverlay = document.getElementById('loading-overlay');
    const apiKeyInput = document.getElementById('api-key-input');
    const saveKeyBtn = document.getElementById('save-key-btn');
    const historyToggle = document.getElementById('history-toggle');
    const closeHistory = document.getElementById('close-history');
    const historySidebar = document.getElementById('history-sidebar');
    const historyList = document.getElementById('history-list');

    // --- State ---
    let currentMode = 'paraphrase';
    let lastAiResponse = '';
    let lastUserQuery = '';
    let extractedPdfText = '';

    // --- Initialization ---
    createParticles();
    loadApiKey();
    userInput.addEventListener('input', () => {
        charCount.textContent = `${userInput.value.length} characters`;
        autoResizeInput();
    });

    sendBtn.addEventListener('click', handleSend);
    userInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSend();
        }
    });

    pdfUpload.addEventListener('change', handlePdfUpload);
    saveKeyBtn.addEventListener('click', saveApiKey);
    historyToggle.addEventListener('click', () => historySidebar.classList.toggle('closed'));
    closeHistory.addEventListener('click', () => historySidebar.classList.add('closed'));
    downloadPdfBtn.addEventListener('click', downloadLastResultAsPdf);

    pills.forEach(pill => {
        pill.addEventListener('click', () => {
            pills.forEach(p => p.classList.remove('active'));
            pill.classList.add('active');
            currentMode = pill.dataset.mode;
        });
    });

    // --- Functions ---

    function autoResizeInput() {
        userInput.style.height = 'auto';
        userInput.style.height = (userInput.scrollHeight) + 'px';
    }

    function appendMessage(sender, text) {
        // Remove welcome message on first interaction
        const welcome = document.querySelector('.welcome-message');
        if (welcome) welcome.remove();

        const msgDiv = document.createElement('div');
        msgDiv.className = `message ${sender === 'user' ? 'user-message' : 'ai-message markdown-body'}`;
        
        msgDiv.innerHTML = `
            <span class="sender">${sender === 'user' ? 'You' : 'RCB AI'}</span>
            <div class="bubble">${sender === 'ai' ? marked.parse(text) : text}</div>
            ${sender === 'ai' ? `
                <div class="message-actions">
                    <button class="msg-action-btn copy-msg"><i class="fa-regular fa-copy"></i> Copy</button>
                    <button class="msg-action-btn download-msg"><i class="fa-solid fa-file-pdf"></i> Save</button>
                </div>
            ` : ''}
        `;

        chatHistory.appendChild(msgDiv);
        chatHistory.scrollTop = chatHistory.scrollHeight;

        if (sender === 'ai') {
            msgDiv.querySelector('.copy-msg').addEventListener('click', () => {
                navigator.clipboard.writeText(text);
                const btn = msgDiv.querySelector('.copy-msg');
                btn.innerHTML = '<i class="fa-solid fa-check"></i> Copied';
                setTimeout(() => btn.innerHTML = '<i class="fa-regular fa-copy"></i> Copy', 2000);
            });
            msgDiv.querySelector('.download-msg').addEventListener('click', () => {
                downloadLastResultAsPdf();
            });
        }
        return msgDiv;
    }

    async function handleSend() {
        const text = userInput.value.trim();
        if (!text && !extractedPdfText) return;

        const query = text || (extractedPdfText ? "Analyze the uploaded PDF" : "");
        appendMessage('user', query);
        userInput.value = '';
        autoResizeInput();

        loadingOverlay.classList.remove('hidden');
        
        // --- Add Typing Indicator ---
        const typingDiv = document.createElement('div');
        typingDiv.className = 'message ai-message typing-indicator';
        typingDiv.innerHTML = `<span class="sender">RCB AI</span><div class="bubble">Thinking...</div>`;
        chatHistory.appendChild(typingDiv);
        chatHistory.scrollTop = chatHistory.scrollHeight;

        let aiMsgDiv = null;
        let aiBubble = null;
        let fullContent = "";

        try {
            const apiKey = apiKeyInput.value.trim();
            const response = await fetch('/api/paraphrase', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    text: extractedPdfText || text, 
                    mode: currentMode,
                    api_key: apiKey,
                    question: text 
                })
            });

            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.error || 'Server error');
            }

            if (typingDiv) typingDiv.remove();

            const reader = response.body.getReader();
            const decoder = new TextDecoder();

            while (true) {
                const { value, done } = await reader.read();
                if (done) break;

                const chunk = decoder.decode(value, { stream: true });
                const lines = chunk.split("\n\n");

                for (const line of lines) {
                    if (line.startsWith("data: ")) {
                        const data = JSON.parse(line.substring(6));

                        if (data.error) {
                            throw new Error(data.error);
                        }

                        if (data.content) {
                            if (!aiMsgDiv) {
                                aiMsgDiv = appendMessage('ai', '');
                                aiBubble = aiMsgDiv.querySelector('.bubble');
                                loadingOverlay.classList.add('hidden');
                            }
                            fullContent += data.content;
                            aiBubble.innerHTML = marked.parse(fullContent);
                            chatHistory.scrollTop = chatHistory.scrollHeight;
                        }

                        if (data.meta) {
                            const engineStatus = document.getElementById('engine-status');
                            const engineText = document.getElementById('engine-text');
                            engineStatus.className = `engine-status ${data.engine}`;
                            engineText.textContent = data.engine === 'openrouter' ? 'OpenRouter AI' : 'Demo Mode';
                            
                            lastAiResponse = fullContent;
                            lastUserQuery = query;
                            downloadPdfBtn.classList.remove('hidden');
                            saveToHistory(query, fullContent, currentMode);
                        }
                    }
                }
            }

        } catch (error) {
            if (typingDiv) typingDiv.remove();
            loadingOverlay.classList.add('hidden');
            const errMsg = error.message.includes("OpenRouter API Key missing") 
                ? `${error.message}\n\n(Please enter your API Key in the top right to enable live responses)`
                : `Error: ${error.message}`;
            appendMessage('ai', errMsg);
        } finally {
            extractedPdfText = ''; 
        }
    }

    async function handlePdfUpload(e) {
        const file = e.target.files[0];
        if (!file) return;

        loadingOverlay.classList.remove('hidden');
        const formData = new FormData();
        formData.append('file', file);

        try {
            const response = await fetch('/api/extract-pdf', {
                method: 'POST',
                body: formData
            });
            const data = await response.json();
            if (response.ok) {
                extractedPdfText = data.text;
                appendMessage('user', `Uploaded PDF: ${file.name}`);
                appendMessage('ai', "I've received your PDF. What would you like me to do with it? (Choose Summarize, Q&A, etc. then click Send)");
            } else {
                alert(data.error);
            }
        } catch (err) {
            alert('PDF Upload failed');
        } finally {
            loadingOverlay.classList.add('hidden');
            pdfUpload.value = '';
        }
    }

    function downloadLastResultAsPdf() {
        if (!lastAiResponse) return;
        const { jsPDF } = window.jspdf;
        const doc = new jsPDF();
        
        doc.setFont("helvetica", "bold");
        doc.setFontSize(22);
        doc.setTextColor(200, 0, 0); 
        doc.text("rcb AI - REPORT", 20, 20);
        
        doc.setFontSize(10);
        doc.setTextColor(100);
        doc.text(`Generated: ${new Date().toLocaleString()}`, 20, 30);
        doc.line(20, 35, 190, 35);

        doc.setFontSize(14);
        doc.setTextColor(0);
        doc.text("PROCESS:", 20, 45);
        doc.setFontSize(11); doc.setFont("helvetica", "normal");
        doc.text(`- Mode: ${currentMode.toUpperCase()}`, 25, 55);

        let yPos = 70;
        doc.setFont("helvetica", "bold");
        doc.text("SOURCE/QUERY:", 20, yPos);
        doc.setFont("helvetica", "normal"); doc.setFontSize(10);
        const qLines = doc.splitTextToSize(lastUserQuery || "PDF Document", 170);
        doc.text(qLines, 20, yPos + 7);
        
        yPos += 15 + (qLines.length * 5);
        if (yPos > 270) { doc.addPage(); yPos = 20; }

        doc.setFont("helvetica", "bold"); doc.setFontSize(11);
        doc.text("AI RESPONSE:", 20, yPos);
        doc.setFont("helvetica", "normal"); doc.setFontSize(10);
        const cleanContent = lastAiResponse.replace(/[#*`]/g, ''); // Crude markdown cleaning for PDF
        const rLines = doc.splitTextToSize(cleanContent, 170);
        doc.text(rLines, 20, yPos + 7);

        doc.save(`rcbAI_${Date.now()}.pdf`);
    }

    function saveApiKey() {
        const key = apiKeyInput.value.trim();
        if (key && key.length > 30) {
            localStorage.setItem('openrouter_api_key', key);
            saveKeyBtn.innerHTML = '<i class="fa-solid fa-check"></i>';
            const warning = document.getElementById('api-key-warning');
            if (warning) warning.style.display = 'none';
            setTimeout(() => saveKeyBtn.innerHTML = '<i class="fa-solid fa-key"></i>', 2000);
        }
    }

    function loadApiKey() {
        const key = localStorage.getItem('openrouter_api_key');
        if (!key) {
            // Check for legacy gemini key (Migration)
            const oldKey = localStorage.getItem('gemini_api_key');
            if (oldKey) {
                localStorage.setItem('openrouter_api_key', oldKey);
                apiKeyInput.value = oldKey;
                localStorage.removeItem('gemini_api_key');
            }
        } else {
            apiKeyInput.value = key;
        }

        // Hide warning if key exists
        if (apiKeyInput.value) {
            const warning = document.getElementById('api-key-warning');
            if (warning) warning.style.display = 'none';
        }
    }

    function saveToHistory(query, response, mode) {
        let history = JSON.parse(localStorage.getItem('rcb_history') || '[]');
        history.unshift({ id: Date.now(), query, response, mode, date: new Date().toLocaleString() });
        localStorage.setItem('rcb_history', JSON.stringify(history.slice(0, 20)));
        loadHistory();
    }

    function loadHistory() {
        const history = JSON.parse(localStorage.getItem('rcb_history') || '[]');
        if (history.length === 0) {
            historyList.innerHTML = '<div class="empty-history">No history yet</div>';
            return;
        }
        historyList.innerHTML = history.map(item => `
            <div class="history-item">
                <div class="summary">${item.query}</div>
                <div class="meta"><span>${item.date}</span> <span>${item.mode}</span></div>
            </div>
        `).join('');
    }

    function createParticles() {
        const container = document.getElementById('particles-js');
        const count = 30;
        for (let i = 0; i < count; i++) {
            const p = document.createElement('div');
            p.className = 'particle';
            const size = Math.random() * 4 + 2;
            p.style.width = `${size}px`;
            p.style.height = `${size}px`;
            p.style.left = `${Math.random() * 100}%`;
            p.style.animationDuration = `${Math.random() * 10 + 10}s`;
            p.style.animationDelay = `${Math.random() * 10}s`;
            p.style.opacity = Math.random() * 0.5;
            container.appendChild(p);
        }
    }

    loadHistory();
});
