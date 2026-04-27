
// Chat Input Component with slash commands, file upload, quick prompts

const QUICK_COMMANDS = [
  { id: 'report', icon: '📊', label: '生成报告 / Generate Report', prompt: '请帮我生成本月IT系统运维状态报告，包括服务可用性、故障统计和建议措施。' },
  { id: 'query', icon: '🔍', label: '系统查询 / System Query', prompt: '查询当前所有生产环境服务的运行状态和资源使用情况。' },
  { id: 'risk', icon: '🛡️', label: '风险检查 / Risk Check', prompt: '对当前系统进行安全风险扫描，识别潜在漏洞和异常访问行为。' },
  { id: 'deploy', icon: '🚀', label: '部署计划 / Deploy Plan', prompt: '制定下周应用版本更新的部署计划，包括回滚策略和测试检查点。' },
  { id: 'alert', icon: '🔔', label: '告警汇总 / Alert Summary', prompt: '汇总今日所有系统告警事件，按严重程度分类并给出处理建议。' },
  { id: 'capacity', icon: '📈', label: '容量规划 / Capacity Planning', prompt: '分析未来3个月的系统容量需求，给出扩容建议和成本预估。' },
];

const ChatInput = ({ theme, onSend, disabled, currentAgent }) => {
  const [value, setValue] = React.useState('');
  const [showCommands, setShowCommands] = React.useState(false);
  const [files, setFiles] = React.useState([]);
  const [dragOver, setDragOver] = React.useState(false);
  const textareaRef = React.useRef(null);
  const fileInputRef = React.useRef(null);

  const t = theme || {};
  const bg = t.bg || '#070d1a';
  const surface = t.surface || '#0d1628';
  const accent = t.accent || '#3b82f6';
  const border = t.border || 'rgba(99,140,210,0.18)';
  const text = t.text || '#e2e8f0';
  const subtext = t.subtext || '#94a3b8';

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
    if (e.key === '/') setShowCommands(true);
    if (e.key === 'Escape') setShowCommands(false);
  };

  const handleSend = () => {
    if ((!value.trim() && files.length === 0) || disabled) return;
    onSend({ text: value.trim(), files: [...files] });
    setValue('');
    setFiles([]);
    setShowCommands(false);
  };

  const handleCommand = (cmd) => {
    setValue(cmd.prompt);
    setShowCommands(false);
    textareaRef.current?.focus();
  };

  const handleFileAdd = (e) => {
    const newFiles = Array.from(e.target.files || []).map(f => ({
      name: f.name, size: formatSize(f.size),
      type: f.name.endsWith('.pdf') ? 'pdf' : f.name.match(/\.(xlsx|xls|csv)$/) ? 'excel' : 'doc'
    }));
    setFiles(prev => [...prev, ...newFiles]);
  };

  const formatSize = (bytes) => {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024*1024) return (bytes/1024).toFixed(1) + ' KB';
    return (bytes/(1024*1024)).toFixed(1) + ' MB';
  };

  // Auto-resize textarea
  React.useEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = 'auto';
    ta.style.height = Math.min(ta.scrollHeight, 160) + 'px';
  }, [value]);

  const canSend = (value.trim() || files.length > 0) && !disabled;

  return (
    <div style={{ padding:'12px 20px 16px', background: bg, borderTop:`1px solid ${border}` }}>
      <div style={{ maxWidth:780, margin:'0 auto', position:'relative' }}>

        {/* Slash Command Palette */}
        {showCommands && (
          <div style={{
            position:'absolute', bottom:'100%', left:0, right:0, marginBottom:8,
            background: surface, border:`1px solid ${border}`, borderRadius:12,
            boxShadow:'0 -8px 32px rgba(0,0,0,0.4)', overflow:'hidden', zIndex:100
          }}>
            <div style={{ padding:'10px 14px 6px', borderBottom:`1px solid ${border}`, display:'flex', justifyContent:'space-between', alignItems:'center' }}>
              <span style={{ color:subtext, fontSize:11, fontWeight:600, letterSpacing:'0.07em', textTransform:'uppercase' }}>快捷指令 / Quick Commands</span>
              <button onClick={() => setShowCommands(false)} style={{ background:'none', border:'none', color:subtext, cursor:'pointer', fontSize:16, lineHeight:1 }}>×</button>
            </div>
            <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:1, padding:6 }}>
              {QUICK_COMMANDS.map(cmd => (
                <button key={cmd.id} onClick={() => handleCommand(cmd)} style={{
                  display:'flex', alignItems:'center', gap:10, padding:'10px 12px',
                  background:'transparent', border:'none', borderRadius:8, cursor:'pointer', textAlign:'left',
                  transition:'background 0.12s'
                }}
                onMouseEnter={e => e.currentTarget.style.background = `${accent}14`}
                onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                >
                  <span style={{ fontSize:18 }}>{cmd.icon}</span>
                  <span style={{ color:text, fontSize:12, fontWeight:500 }}>{cmd.label}</span>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* File chips */}
        {files.length > 0 && (
          <div style={{ display:'flex', flexWrap:'wrap', gap:6, marginBottom:8 }}>
            {files.map((f, i) => (
              <div key={i} style={{
                display:'flex', alignItems:'center', gap:6, padding:'4px 10px',
                background:`${accent}14`, border:`1px solid ${accent}30`, borderRadius:20
              }}>
                <span style={{ fontSize:13 }}>{f.type === 'pdf' ? '📄' : f.type === 'excel' ? '📊' : '📎'}</span>
                <span style={{ color:text, fontSize:11, fontWeight:500 }}>{f.name}</span>
                <span style={{ color:subtext, fontSize:10 }}>{f.size}</span>
                <button onClick={() => setFiles(prev => prev.filter((_,j)=>j!==i))} style={{
                  background:'none', border:'none', color:subtext, cursor:'pointer', fontSize:14, lineHeight:1, padding:0
                }}>×</button>
              </div>
            ))}
          </div>
        )}

        {/* Main Input Box */}
        <div style={{
          background: surface, border:`1px solid ${dragOver ? accent : border}`,
          borderRadius:14, overflow:'hidden', transition:'border-color 0.15s, box-shadow 0.15s',
          boxShadow: dragOver ? `0 0 0 3px ${accent}22` : '0 2px 12px rgba(0,0,0,0.2)'
        }}
        onDragOver={e => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={e => {
          e.preventDefault(); setDragOver(false);
          const dropped = Array.from(e.dataTransfer.files).map(f => ({
            name: f.name, size: formatSize(f.size),
            type: f.name.endsWith('.pdf') ? 'pdf' : f.name.match(/\.(xlsx|xls|csv)$/) ? 'excel' : 'doc'
          }));
          setFiles(prev => [...prev, ...dropped]);
        }}
        >
          <textarea
            ref={textareaRef}
            value={value}
            onChange={e => { setValue(e.target.value); if(!e.target.value) setShowCommands(false); }}
            onKeyDown={handleKeyDown}
            placeholder={`向 ${currentAgent?.name || 'AI Agent'} 发送消息... 输入 / 使用快捷指令`}
            disabled={disabled}
            rows={1}
            style={{
              width:'100%', background:'none', border:'none', outline:'none',
              padding:'14px 16px 0', color:text, fontSize:14, lineHeight:1.6,
              resize:'none', boxSizing:'border-box', fontFamily:'inherit',
              minHeight:48, maxHeight:160
            }}
          />
          <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', padding:'8px 12px 10px' }}>
            <div style={{ display:'flex', gap:4 }}>
              {/* File Upload */}
              <input ref={fileInputRef} type="file" multiple style={{ display:'none' }} onChange={handleFileAdd} />
              <button onClick={() => fileInputRef.current?.click()} title="上传附件" style={{
                background:'none', border:'none', cursor:'pointer', color:subtext, padding:'6px 8px', borderRadius:7,
                display:'flex', alignItems:'center', gap:4, fontSize:12, transition:'color 0.15s'
              }}
              onMouseEnter={e => e.currentTarget.style.color = accent}
              onMouseLeave={e => e.currentTarget.style.color = subtext}
              >
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
              </button>
              {/* Quick Commands */}
              <button onClick={() => setShowCommands(!showCommands)} title="快捷指令" style={{
                background: showCommands ? `${accent}18` : 'none', border:'none', cursor:'pointer',
                color: showCommands ? accent : subtext, padding:'6px 8px', borderRadius:7,
                display:'flex', alignItems:'center', gap:4, fontSize:12, transition:'all 0.15s'
              }}
              onMouseEnter={e => { e.currentTarget.style.color = accent; e.currentTarget.style.background = `${accent}14`; }}
              onMouseLeave={e => { if(!showCommands) { e.currentTarget.style.color = subtext; e.currentTarget.style.background = 'none'; }}}
              >
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/></svg>
                <span style={{ fontSize:11 }}>/ 指令</span>
              </button>
            </div>
            <div style={{ display:'flex', alignItems:'center', gap:8 }}>
              <span style={{ color:subtext, fontSize:10 }}>Shift+Enter 换行</span>
              <button onClick={handleSend} disabled={!canSend} style={{
                background: canSend ? `linear-gradient(135deg, ${accent}, ${accent}cc)` : `${accent}30`,
                border:'none', borderRadius:8, padding:'7px 16px',
                color: canSend ? 'white' : `${accent}66`, fontSize:13, fontWeight:600,
                cursor: canSend ? 'pointer' : 'not-allowed', display:'flex', alignItems:'center', gap:6,
                transition:'all 0.15s', boxShadow: canSend ? `0 2px 12px ${accent}44` : 'none'
              }}>
                <span>发送</span>
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
              </button>
            </div>
          </div>
        </div>

        <div style={{ textAlign:'center', marginTop:7 }}>
          <span style={{ color:subtext, fontSize:10, opacity:0.5 }}>JARVIS 可能出现错误，重要决策请人工复核 · AI may make mistakes, verify critical decisions</span>
        </div>
      </div>
    </div>
  );
};

Object.assign(window, { ChatInput, QUICK_COMMANDS });
