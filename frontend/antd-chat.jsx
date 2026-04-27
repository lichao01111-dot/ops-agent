
// antd-chat.jsx — Chat messages, ToolCallCard, ChatInput using Ant Design v5

// ─── Tool Call Card ────────────────────────────────────────────────────────
const AntdToolCallCard = ({ toolCall }) => {
  const { Collapse, Tag, Typography, Space } = antd;
  const { CheckCircleFilled, LoadingOutlined, CloseCircleFilled, CodeOutlined } = icons;
  const { Text } = Typography;

  const statusMap = {
    running: { color:'processing', icon:<LoadingOutlined spin/>, label:'执行中' },
    success: { color:'success',    icon:<CheckCircleFilled/>,   label:'成功' },
    error:   { color:'error',      icon:<CloseCircleFilled/>,   label:'失败' },
  };
  const s = statusMap[toolCall.status] || statusMap.success;

  const toolIcons = { database_query:'🗄️', api_call:'🔌', report_generate:'📊', file_read:'📄', send_notification:'🔔', risk_check:'🛡️', system_query:'💻', data_export:'📦', user_lookup:'👤' };
  const icon = toolIcons[toolCall.tool] || '⚙️';

  const label = (
    <Space size={8} style={{ width:'100%' }}>
      <span style={{ fontSize:15 }}>{icon}</span>
      <Text code style={{ fontSize:12 }}>{toolCall.tool}</Text>
      <Text type="secondary" style={{ fontSize:11 }}>{toolCall.description}</Text>
      <Tag color={s.color} icon={s.icon} style={{ marginLeft:'auto', fontSize:10 }}>{s.label}</Tag>
    </Space>
  );

  return (
    <Collapse
      size="small"
      style={{ marginTop:8, borderRadius:8, fontSize:12, background:'#fafafa', border:'1px solid #f0f0f0' }}
      items={[{
        key:'1', label,
        children: (
          <div style={{ display:'flex', gap:12 }}>
            <div style={{ flex:1 }}>
              <Text style={{ fontSize:10, color:'#94a3b8', fontWeight:600, textTransform:'uppercase', letterSpacing:'0.07em', display:'block', marginBottom:4 }}>输入参数 / Input</Text>
              <pre style={{
                background:'#1e293b', borderRadius:6, padding:'10px 12px',
                color:'#a5f3fc', fontSize:11, fontFamily:'JetBrains Mono, monospace',
                margin:0, overflowX:'auto', lineHeight:1.6
              }}>{JSON.stringify(toolCall.input, null, 2)}</pre>
            </div>
            {toolCall.output && (
              <div style={{ flex:1 }}>
                <Text style={{ fontSize:10, color:'#94a3b8', fontWeight:600, textTransform:'uppercase', letterSpacing:'0.07em', display:'block', marginBottom:4 }}>输出结果 / Output</Text>
                <pre style={{
                  background:'#1e293b', borderRadius:6, padding:'10px 12px',
                  color:'#86efac', fontSize:11, fontFamily:'JetBrains Mono, monospace',
                  margin:0, overflowX:'auto', lineHeight:1.6
                }}>{typeof toolCall.output==='string' ? toolCall.output : JSON.stringify(toolCall.output, null, 2)}</pre>
              </div>
            )}
          </div>
        )
      }]}
    />
  );
};

// ─── Message Bubble ─────────────────────────────────────────────────────────
const AntdMessageBubble = ({ msg, isStreaming, userInitial }) => {
  const { Avatar, Typography, Space, Tag } = antd;
  const { FileOutlined, FilePdfOutlined, FileExcelOutlined } = icons;
  const { Text } = Typography;
  const isUser = msg.role === 'user';

  const renderContent = (text) => {
    if (!text) return null;
    return text.split('\n').map((line, i) => {
      if (line.startsWith('### ')) return <div key={i} style={{ fontWeight:700, fontSize:13, color:'#0f172a', margin:'8px 0 2px' }}>{line.slice(4)}</div>;
      if (line.startsWith('## '))  return <div key={i} style={{ fontWeight:700, fontSize:14, color:'#0f172a', margin:'10px 0 3px' }}>{line.slice(3)}</div>;
      if (line.startsWith('- '))   return <div key={i} style={{ paddingLeft:12, position:'relative', lineHeight:1.7, color: isUser?'white':'#334155' }}><span style={{ position:'absolute',left:0,color:isUser?'rgba(255,255,255,0.7)':'#1d4ed8' }}>•</span>{line.slice(2)}</div>;
      if (line === '')              return <div key={i} style={{ height:5 }}/>;
      const parts = line.split(/(\*\*[^*]+\*\*)/g);
      return (
        <div key={i} style={{ lineHeight:1.75, color: isUser?'white':'#334155' }}>
          {parts.map((p,j) => p.startsWith('**') ? <strong key={j} style={{ color: isUser?'white':'#0f172a' }}>{p.slice(2,-2)}</strong> : p)}
        </div>
      );
    });
  };

  return (
    <div style={{
      display:'flex', flexDirection: isUser ? 'row-reverse' : 'row',
      alignItems:'flex-start', gap:10, marginBottom:18,
      animation:'fadeInUp 0.2s ease-out'
    }}>
      {/* Avatar */}
      {!isUser ? (
        <Avatar size={34} style={{
          background:`${msg.agentColor || '#1d4ed8'}20`,
          border:`1px solid ${msg.agentColor || '#1d4ed8'}40`,
          fontSize:16, flexShrink:0, marginTop:2
        }}>{msg.agentIcon || '🤖'}</Avatar>
      ) : (
        <Avatar size={34} style={{
          background:'linear-gradient(135deg,#1d4ed8,#3b82f6)',
          color:'white', fontWeight:700, flexShrink:0, marginTop:2
        }}>{userInitial || 'U'}</Avatar>
      )}

      <div style={{ maxWidth:'72%', minWidth:100 }}>
        {/* Name + time */}
        {!isUser && (
          <div style={{ display:'flex', alignItems:'center', gap:8, marginBottom:4 }}>
            <Text style={{ fontSize:12, fontWeight:600, color: msg.agentColor || '#1d4ed8' }}>{msg.agentName || 'AI'}</Text>
            <Text type="secondary" style={{ fontSize:10 }}>{msg.time}</Text>
            {isStreaming && (
              <span style={{ display:'flex', gap:3 }}>
                {[0,1,2].map(i=><span key={i} style={{ width:4,height:4,borderRadius:'50%',background:msg.agentColor||'#1d4ed8',display:'inline-block',animation:`bounce 1.2s ${i*0.2}s ease-in-out infinite` }}/>)}
              </span>
            )}
          </div>
        )}

        {/* Bubble */}
        <div style={{
          padding:'11px 15px',
          background: isUser
            ? 'linear-gradient(135deg,#1d4ed8,#3b82f6)'
            : '#ffffff',
          border: isUser ? 'none' : '1px solid #e8edf5',
          borderRadius: isUser ? '16px 4px 16px 16px' : '4px 16px 16px 16px',
          boxShadow: isUser ? '0 4px 16px rgba(29,78,216,0.25)' : '0 1px 4px rgba(0,0,0,0.06)',
          fontSize: 13, lineHeight: 1.7
        }}>
          {renderContent(msg.content)}
          {isStreaming && <span style={{ display:'inline-block',width:2,height:14,background:'#1d4ed8',marginLeft:2,animation:'blink 0.7s step-end infinite',verticalAlign:'middle' }}/>}
        </div>

        {/* Tool Calls */}
        {msg.toolCalls?.map((tc,i) => <AntdToolCallCard key={i} toolCall={tc} />)}

        {/* Files */}
        {msg.files?.map((f,i) => (
          <div key={i} style={{
            marginTop:8, display:'flex', alignItems:'center', gap:10, padding:'8px 12px',
            background:'#f8fafc', border:'1px solid #e8edf5', borderRadius:8
          }}>
            <div style={{ width:30,height:30,borderRadius:7,background:'rgba(29,78,216,0.1)',display:'flex',alignItems:'center',justifyContent:'center',fontSize:15 }}>
              {f.type==='pdf'?'📄':f.type==='excel'?'📊':'📎'}
            </div>
            <div>
              <div style={{ fontSize:12,fontWeight:500,color:'#0f172a' }}>{f.name}</div>
              <div style={{ fontSize:10,color:'#94a3b8' }}>{f.size}</div>
            </div>
          </div>
        ))}

        {isUser && <div style={{ textAlign:'right', marginTop:3 }}><Text type="secondary" style={{ fontSize:10 }}>{msg.time}</Text></div>}
      </div>
    </div>
  );
};

// ─── Thinking Indicator ──────────────────────────────────────────────────────
const AntdThinking = ({ agent }) => {
  const { Avatar, Typography } = antd;
  const { Text } = Typography;
  return (
    <div style={{ display:'flex', alignItems:'flex-start', gap:10, marginBottom:18 }}>
      <Avatar size={34} style={{ background:`${agent?.color||'#1d4ed8'}20`,border:`1px solid ${agent?.color||'#1d4ed8'}30`,fontSize:16,flexShrink:0,marginTop:2 }}>{agent?.icon||'🤖'}</Avatar>
      <div>
        <Text style={{ fontSize:12,fontWeight:600,color:agent?.color||'#1d4ed8',display:'block',marginBottom:4 }}>{agent?.name||'AI'}</Text>
        <div style={{ padding:'12px 16px',background:'#fff',border:'1px solid #e8edf5',borderRadius:'4px 16px 16px 16px',boxShadow:'0 1px 4px rgba(0,0,0,0.06)',display:'flex',alignItems:'center',gap:10 }}>
          <Text type="secondary" style={{ fontSize:13 }}>正在思考中</Text>
          <span style={{ display:'flex',gap:4 }}>
            {[0,1,2].map(i=><span key={i} style={{ width:6,height:6,borderRadius:'50%',background:agent?.color||'#1d4ed8',display:'inline-block',animation:`bounce 1.2s ${i*0.2}s ease-in-out infinite` }}/>)}
          </span>
        </div>
      </div>
    </div>
  );
};

// ─── Chat Messages Area ───────────────────────────────────────────────────────
const AntdChatMessages = ({ messages, isThinking, currentAgent, messagesEndRef, streamingId, userInitial }) => (
  <div style={{ flex:1, overflowY:'auto', padding:'24px 0', background:'#f8fafc', scrollbarWidth:'thin', scrollbarColor:'#e2e8f0 transparent' }}>
    <div style={{ maxWidth:800, margin:'0 auto', padding:'0 24px' }}>
      {messages.map(msg => (
        <AntdMessageBubble key={msg.id} msg={msg} isStreaming={msg.id===streamingId} userInitial={userInitial}/>
      ))}
      {isThinking && <AntdThinking agent={currentAgent}/>}
      <div ref={messagesEndRef}/>
    </div>
  </div>
);

// ─── Quick Commands ───────────────────────────────────────────────────────────
const ANTD_COMMANDS = [
  { id:'report',   icon:'📊', label:'生成运维报告',  prompt:'请帮我生成本月IT系统运维状态报告，包括服务可用性、故障统计和建议措施。' },
  { id:'query',    icon:'🔍', label:'系统状态查询',  prompt:'查询当前所有生产环境服务的运行状态和资源使用情况。' },
  { id:'risk',     icon:'🛡️', label:'安全风险扫描',  prompt:'对当前系统进行安全风险扫描，识别潜在漏洞和异常访问行为。' },
  { id:'deploy',   icon:'🚀', label:'部署计划制定',  prompt:'制定下周应用版本更新的部署计划，包括回滚策略和测试检查点。' },
  { id:'alert',    icon:'🔔', label:'告警事件汇总',  prompt:'汇总今日所有系统告警事件，按严重程度分类并给出处理建议。' },
  { id:'capacity', icon:'📈', label:'容量规划分析',  prompt:'分析未来3个月的系统容量需求，给出扩容建议和成本预估。' },
];

// ─── Chat Input ───────────────────────────────────────────────────────────────
const AntdChatInput = ({ onSend, disabled, currentAgent }) => {
  const { Button, Upload, Tooltip, Typography, Popover, Card, Space } = antd;
  const { SendOutlined, PaperClipOutlined, ThunderboltOutlined, DeleteOutlined } = icons;
  const { Text } = Typography;

  const [value, setValue] = React.useState('');
  const [files, setFiles] = React.useState([]);
  const [showCommands, setShowCommands] = React.useState(false);
  const textareaRef = React.useRef(null);

  React.useEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = 'auto';
    ta.style.height = Math.min(ta.scrollHeight, 160) + 'px';
  }, [value]);

  const handleSend = () => {
    if ((!value.trim() && files.length===0) || disabled) return;
    onSend({ text: value.trim(), files: [...files] });
    setValue(''); setFiles([]);
  };

  const handleKey = (e) => {
    if (e.key==='Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); }
  };

  const commandMenu = (
    <div style={{ width:360 }}>
      <Text style={{ fontSize:11,color:'#94a3b8',fontWeight:600,letterSpacing:'0.07em',textTransform:'uppercase',display:'block',marginBottom:8 }}>快捷指令 / Quick Commands</Text>
      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:6 }}>
        {ANTD_COMMANDS.map(cmd => (
          <div key={cmd.id} onClick={() => { setValue(cmd.prompt); setShowCommands(false); textareaRef.current?.focus(); }}
            style={{ display:'flex',alignItems:'center',gap:8,padding:'9px 10px',borderRadius:8,cursor:'pointer',transition:'background 0.12s' }}
            onMouseEnter={e=>e.currentTarget.style.background='#f0f4ff'}
            onMouseLeave={e=>e.currentTarget.style.background='transparent'}
          >
            <span style={{ fontSize:18 }}>{cmd.icon}</span>
            <Text style={{ fontSize:12,fontWeight:500,color:'#334155' }}>{cmd.label}</Text>
          </div>
        ))}
      </div>
    </div>
  );

  return (
    <div style={{ padding:'12px 20px 16px', background:'#fff', borderTop:'1px solid #f0f0f0' }}>
      <div style={{ maxWidth:800, margin:'0 auto' }}>
        {/* File chips */}
        {files.length > 0 && (
          <div style={{ display:'flex',flexWrap:'wrap',gap:6,marginBottom:8 }}>
            {files.map((f,i) => (
              <div key={i} style={{ display:'flex',alignItems:'center',gap:6,padding:'3px 10px',background:'#f0f4ff',border:'1px solid #dde5ff',borderRadius:20 }}>
                <span style={{ fontSize:12 }}>{f.type==='pdf'?'📄':f.type==='excel'?'📊':'📎'}</span>
                <Text style={{ fontSize:11,fontWeight:500,color:'#1d4ed8' }}>{f.name}</Text>
                <Button type="text" size="small" icon={<DeleteOutlined/>} onClick={()=>setFiles(p=>p.filter((_,j)=>j!==i))}
                  style={{ color:'#94a3b8',padding:'0 2px',height:'auto',minWidth:'auto' }}/>
              </div>
            ))}
          </div>
        )}

        {/* Input Box */}
        <div style={{
          background:'#fff', border:'1px solid #e2e8f0', borderRadius:14,
          boxShadow:'0 2px 12px rgba(0,0,0,0.06)', overflow:'hidden', transition:'border-color 0.15s, box-shadow 0.15s'
        }}
        onFocusCapture={e => { e.currentTarget.style.borderColor='#1d4ed8'; e.currentTarget.style.boxShadow='0 0 0 3px rgba(29,78,216,0.08)'; }}
        onBlurCapture={e => { e.currentTarget.style.borderColor='#e2e8f0'; e.currentTarget.style.boxShadow='0 2px 12px rgba(0,0,0,0.06)'; }}
        >
          <textarea
            ref={textareaRef}
            value={value}
            onChange={e => setValue(e.target.value)}
            onKeyDown={handleKey}
            placeholder={`向 ${currentAgent?.name || 'AI Agent'} 发送消息...  输入 / 使用快捷指令`}
            disabled={disabled}
            rows={1}
            style={{
              width:'100%', border:'none', outline:'none', resize:'none',
              padding:'14px 16px 0', fontSize:14, lineHeight:1.6, color:'#0f172a',
              fontFamily:"'Inter','Noto Sans SC',sans-serif", minHeight:48, maxHeight:160,
              background:'transparent', boxSizing:'border-box'
            }}
          />
          <div style={{ display:'flex',alignItems:'center',justifyContent:'space-between',padding:'8px 12px 10px' }}>
            <Space size={4}>
              {/* File Upload */}
              <Upload
                showUploadList={false}
                multiple
                beforeUpload={(file) => {
                  const ext = file.name.split('.').pop().toLowerCase();
                  const type = ext==='pdf' ? 'pdf' : ['xlsx','xls','csv'].includes(ext) ? 'excel' : 'doc';
                  const size = file.size < 1024*1024 ? (file.size/1024).toFixed(1)+' KB' : (file.size/1024/1024).toFixed(1)+' MB';
                  setFiles(p => [...p, { name:file.name, size, type }]);
                  return false;
                }}
              >
                <Tooltip title="上传附件 / Attach">
                  <Button type="text" icon={<PaperClipOutlined/>} size="small" style={{ color:'#94a3b8', borderRadius:7 }}/>
                </Tooltip>
              </Upload>

              {/* Quick Commands */}
              <Popover
                content={commandMenu} trigger="click" placement="topLeft"
                open={showCommands} onOpenChange={setShowCommands}
                overlayStyle={{ borderRadius:12 }}
                overlayInnerStyle={{ borderRadius:12, padding:'14px 16px' }}
              >
                <Tooltip title="快捷指令 / Commands">
                  <Button type={showCommands?'primary':'text'} icon={<ThunderboltOutlined/>} size="small"
                    style={{ borderRadius:7, color: showCommands?undefined:'#94a3b8' }}>
                    <span style={{ fontSize:11 }}>/ 指令</span>
                  </Button>
                </Tooltip>
              </Popover>
            </Space>

            <Space size={10} align="center">
              <Text type="secondary" style={{ fontSize:10 }}>Shift+Enter 换行</Text>
              <Button
                type="primary" icon={<SendOutlined/>} onClick={handleSend}
                disabled={(!value.trim() && files.length===0) || disabled}
                style={{ borderRadius:8, fontWeight:600, paddingInline:16 }}
              >发送</Button>
            </Space>
          </div>
        </div>

        <div style={{ textAlign:'center', marginTop:6 }}>
          <Text type="secondary" style={{ fontSize:10 }}>JARVIS 可能出现错误，重要决策请人工复核 · AI responses may contain errors</Text>
        </div>
      </div>
    </div>
  );
};

Object.assign(window, { AntdChatMessages, AntdChatInput, AntdThinking, AntdToolCallCard, ANTD_COMMANDS });
