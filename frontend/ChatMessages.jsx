
// Chat Messages Component — includes ToolCallCard, MessageBubble, ThinkingIndicator

const ToolCallCard = ({ toolCall, theme }) => {
  const [expanded, setExpanded] = React.useState(false);
  const t = theme || {};
  const surface = t.surface || '#0d1628';
  const border = t.border || 'rgba(99,140,210,0.18)';
  const text = t.text || '#e2e8f0';
  const subtext = t.subtext || '#94a3b8';

  const statusColor = { running: '#f59e0b', success: '#10b981', error: '#f87171' }[toolCall.status] || '#94a3b8';
  const statusLabel = { running: '执行中', success: '成功', error: '失败' }[toolCall.status] || '';
  const toolIcons = {
    database_query: '🗄️', api_call: '🔌', report_generate: '📊',
    file_read: '📄', send_notification: '🔔', risk_check: '🛡️',
    system_query: '💻', data_export: '📦', user_lookup: '👤', default: '⚙️'
  };
  const icon = toolIcons[toolCall.tool] || toolIcons.default;

  return (
    <div style={{
      background: `${surface}cc`, border: `1px solid ${border}`, borderRadius: 10,
      overflow:'hidden', marginTop: 8, fontSize: 12
    }}>
      <button onClick={() => setExpanded(!expanded)} style={{
        width:'100%', display:'flex', alignItems:'center', gap:10, padding:'10px 14px',
        background:'none', border:'none', cursor:'pointer', textAlign:'left'
      }}>
        <span style={{ fontSize: 16 }}>{icon}</span>
        <div style={{ flex:1 }}>
          <span style={{ color: text, fontWeight: 600, fontFamily:'monospace', fontSize: 12 }}>{toolCall.tool}</span>
          <span style={{ color: subtext, marginLeft: 8, fontSize: 11 }}>{toolCall.description}</span>
        </div>
        <div style={{ display:'flex', alignItems:'center', gap:6 }}>
          {toolCall.status === 'running' && (
            <div style={{ display:'flex', gap:3 }}>
              {[0,1,2].map(i => (
                <div key={i} style={{
                  width:4, height:4, borderRadius:'50%', background: statusColor,
                  animation: `pulse 1s ${i*0.2}s ease-in-out infinite`
                }}/>
              ))}
            </div>
          )}
          <div style={{
            padding:'2px 8px', borderRadius:4, background:`${statusColor}18`,
            border:`1px solid ${statusColor}44`, color: statusColor, fontSize: 10, fontWeight:600
          }}>{statusLabel}</div>
          <svg style={{ color: subtext, transition:'transform 0.2s', transform: expanded ? 'rotate(180deg)' : 'none' }}
            width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <polyline points="6 9 12 15 18 9"/>
          </svg>
        </div>
      </button>

      {expanded && (
        <div style={{ borderTop: `1px solid ${border}`, padding:'12px 14px', display:'flex', gap:16 }}>
          <div style={{ flex:1 }}>
            <div style={{ color: subtext, fontSize: 10, fontWeight:600, letterSpacing:'0.07em', textTransform:'uppercase', marginBottom:6 }}>输入参数 / Input</div>
            <pre style={{
              background:'rgba(0,0,0,0.25)', border:`1px solid ${border}`, borderRadius:6,
              padding:'10px 12px', color:'#a5f3fc', fontSize:11, fontFamily:'monospace',
              margin:0, overflowX:'auto', lineHeight:1.6
            }}>{JSON.stringify(toolCall.input, null, 2)}</pre>
          </div>
          {toolCall.output && (
            <div style={{ flex:1 }}>
              <div style={{ color: subtext, fontSize: 10, fontWeight:600, letterSpacing:'0.07em', textTransform:'uppercase', marginBottom:6 }}>输出结果 / Output</div>
              <pre style={{
                background:'rgba(0,0,0,0.25)', border:`1px solid ${border}`, borderRadius:6,
                padding:'10px 12px', color:'#86efac', fontSize:11, fontFamily:'monospace',
                margin:0, overflowX:'auto', lineHeight:1.6
              }}>{typeof toolCall.output === 'string' ? toolCall.output : JSON.stringify(toolCall.output, null, 2)}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

const MessageBubble = ({ msg, theme, isStreaming }) => {
  const t = theme || {};
  const surface = t.surface || '#0d1628';
  const accent = t.accent || '#3b82f6';
  const border = t.border || 'rgba(99,140,210,0.18)';
  const text = t.text || '#e2e8f0';
  const subtext = t.subtext || '#94a3b8';
  const isUser = msg.role === 'user';

  // Simple markdown-ish render
  const renderText = (content) => {
    if (!content) return null;
    const lines = content.split('\n');
    return lines.map((line, i) => {
      if (line.startsWith('### ')) return <div key={i} style={{ fontWeight:700, fontSize:14, color:text, margin:'10px 0 4px' }}>{line.slice(4)}</div>;
      if (line.startsWith('## ')) return <div key={i} style={{ fontWeight:700, fontSize:15, color:text, margin:'12px 0 4px' }}>{line.slice(3)}</div>;
      if (line.startsWith('**') && line.endsWith('**')) return <div key={i} style={{ fontWeight:600, color:text }}>{line.slice(2,-2)}</div>;
      if (line.startsWith('- ')) return <div key={i} style={{ paddingLeft:14, position:'relative', color:text, lineHeight:1.7 }}><span style={{ position:'absolute', left:2, color:accent }}>·</span>{line.slice(2)}</div>;
      if (line === '') return <div key={i} style={{ height:6 }}/>;
      // inline bold
      const parts = line.split(/(\*\*[^*]+\*\*)/g);
      return (
        <div key={i} style={{ lineHeight:1.75, color:text }}>
          {parts.map((p, j) => p.startsWith('**') ? <strong key={j}>{p.slice(2,-2)}</strong> : p)}
        </div>
      );
    });
  };

  return (
    <div style={{
      display:'flex', flexDirection: isUser ? 'row-reverse' : 'row',
      alignItems:'flex-start', gap:12, marginBottom: 20,
      animation: 'fadeInUp 0.2s ease-out'
    }}>
      {/* Avatar */}
      {!isUser && (
        <div style={{
          width:34, height:34, borderRadius:9, flexShrink:0, marginTop:2,
          background: `linear-gradient(135deg, ${msg.agentColor || accent}44, ${msg.agentColor || accent}22)`,
          border:`1px solid ${msg.agentColor || accent}40`,
          display:'flex', alignItems:'center', justifyContent:'center', fontSize:16
        }}>{msg.agentIcon || '🤖'}</div>
      )}

      <div style={{ maxWidth:'72%', minWidth:120 }}>
        {/* Header */}
        {!isUser && (
          <div style={{ display:'flex', alignItems:'center', gap:8, marginBottom:5 }}>
            <span style={{ color: msg.agentColor || accent, fontWeight:600, fontSize:12 }}>{msg.agentName || 'AI Assistant'}</span>
            <span style={{ color: subtext, fontSize: 10 }}>{msg.time}</span>
            {isStreaming && <div style={{ display:'flex', gap:2 }}>
              {[0,1,2].map(i => <div key={i} style={{ width:3, height:3, borderRadius:'50%', background:accent, animation:`pulse 1s ${i*0.15}s infinite` }}/>)}
            </div>}
          </div>
        )}

        {/* Bubble */}
        <div style={{
          background: isUser
            ? `linear-gradient(135deg, ${accent}cc, ${accent}aa)`
            : `${surface}ee`,
          border: isUser ? 'none' : `1px solid ${border}`,
          borderRadius: isUser ? '16px 4px 16px 16px' : '4px 16px 16px 16px',
          padding:'12px 16px',
          boxShadow: isUser ? `0 4px 16px ${accent}33` : '0 2px 8px rgba(0,0,0,0.2)',
          fontSize: 13, lineHeight: 1.7
        }}>
          {renderText(msg.content)}
          {isStreaming && <span style={{ display:'inline-block', width:2, height:14, background:accent, marginLeft:2, animation:'blink 0.7s step-end infinite', verticalAlign:'middle' }}/>}
        </div>

        {/* Tool Calls */}
        {msg.toolCalls && msg.toolCalls.map((tc, i) => (
          <ToolCallCard key={i} toolCall={tc} theme={t} />
        ))}

        {/* File Attachments */}
        {msg.files && msg.files.map((f, i) => (
          <div key={i} style={{
            marginTop:8, display:'flex', alignItems:'center', gap:10, padding:'8px 12px',
            background:`${surface}cc`, border:`1px solid ${border}`, borderRadius:8
          }}>
            <div style={{ width:32, height:32, borderRadius:7, background:`${accent}18`, display:'flex', alignItems:'center', justifyContent:'center', fontSize:16 }}>
              {f.type === 'pdf' ? '📄' : f.type === 'excel' ? '📊' : '📎'}
            </div>
            <div>
              <div style={{ color:text, fontSize:12, fontWeight:500 }}>{f.name}</div>
              <div style={{ color:subtext, fontSize:10 }}>{f.size}</div>
            </div>
          </div>
        ))}

        {isUser && <div style={{ color:subtext, fontSize:10, marginTop:4, textAlign:'right' }}>{msg.time}</div>}
      </div>

      {isUser && (
        <div style={{
          width:34, height:34, borderRadius:'50%', flexShrink:0, marginTop:2,
          background:`linear-gradient(135deg, ${accent}88, ${accent}44)`,
          display:'flex', alignItems:'center', justifyContent:'center', color:'white', fontWeight:700, fontSize:13
        }}>{msg.userInitial || 'U'}</div>
      )}
    </div>
  );
};

const ThinkingIndicator = ({ agent, theme }) => {
  const t = theme || {};
  const accent = t.accent || '#3b82f6';
  const surface = t.surface || '#0d1628';
  const border = t.border || 'rgba(99,140,210,0.18)';
  const subtext = t.subtext || '#94a3b8';

  return (
    <div style={{ display:'flex', alignItems:'flex-start', gap:12, marginBottom:20 }}>
      <div style={{
        width:34, height:34, borderRadius:9, flexShrink:0,
        background:`${agent?.color || accent}22`, border:`1px solid ${agent?.color || accent}40`,
        display:'flex', alignItems:'center', justifyContent:'center', fontSize:16
      }}>{agent?.icon || '🤖'}</div>
      <div>
        <div style={{ color:agent?.color || accent, fontWeight:600, fontSize:12, marginBottom:5 }}>{agent?.name || 'AI'}</div>
        <div style={{
          background:`${surface}ee`, border:`1px solid ${border}`, borderRadius:'4px 16px 16px 16px',
          padding:'12px 18px', display:'flex', alignItems:'center', gap:8
        }}>
          <span style={{ color:subtext, fontSize:12 }}>正在思考中</span>
          <div style={{ display:'flex', gap:4 }}>
            {[0,1,2].map(i => (
              <div key={i} style={{
                width:6, height:6, borderRadius:'50%', background:agent?.color || accent,
                animation:`bounce 1.2s ${i*0.2}s ease-in-out infinite`
              }}/>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};

const ChatMessages = ({ messages, isThinking, currentAgent, theme, messagesEndRef, streamingId }) => {
  const t = theme || {};
  const bg = t.bg || '#070d1a';

  return (
    <div style={{
      flex:1, overflowY:'auto', padding:'24px 0', background: bg,
      scrollbarWidth:'thin', scrollbarColor:`rgba(99,140,210,0.2) transparent`
    }}>
      <div style={{ maxWidth:780, margin:'0 auto', padding:'0 24px' }}>
        {messages.map((msg, i) => (
          <MessageBubble key={msg.id} msg={msg} theme={t} isStreaming={msg.id === streamingId} />
        ))}
        {isThinking && <ThinkingIndicator agent={currentAgent} theme={t} />}
        <div ref={messagesEndRef}/>
      </div>
    </div>
  );
};

Object.assign(window, { ChatMessages, ToolCallCard, MessageBubble, ThinkingIndicator });
