# @Time    :2026/4/13
# @Author  :进喜
# @File    :main.py
# @Software:PyCharm

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn
import asyncio
import traceback  # 🚀 加上这个内置库，专门用来打印报错案发现场

# 引入咱们的各个业务模块
from agent import CyberAgent  # 假设内部管理了多个 NPC 实例
from relationship import RelationshipManager
from state_manager import StateManager
from logger import DialogueLogger, logger_instance
from config import settings
from LLMClient import LLMClient

# 创建 FastAPI 应用
app = FastAPI(
    title="赛博小镇后端服务",
    description="基于 AI 的 NPC 对话与环境模拟系统",
    version="1.0.0"
)

# 配置 CORS, 允许 Godot 前端跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class AgentManager:
    """NPC 大脑管家：负责批量创建和管理所有的 CyberAgent 实例"""
    def __init__(self):
        self.agents = {}  # 这是一个字典，专门用来装张三、李四的大脑

    def initialize_npcs(self, npc_state_list):
        """根据花名册，给每个人发一个大脑"""
        for npc in npc_state_list:
            npc_id = npc["npc_id"]
            self.agents[npc_id] = CyberAgent(
                npc_id=npc_id,
                name=npc["name"],
                role=npc["role"],
                # 如果状态里没有写性格，咱们给个默认值兜底
                personality=npc.get("personality", "一个普通的赛博小镇居民")
            )

    def get_agent(self, npc_id: str) -> CyberAgent:
        """根据 ID 提取出对应的大脑"""
        return self.agents.get(npc_id)

# ==========================================
# 初始化各个管理器 (全局单例)
# ==========================================
agent_manager = AgentManager() # 👈 注意这里！换成咱们刚写的管家类了
shared_llm = LLMClient()
relationship_manager = RelationshipManager(llm_client=shared_llm)  # 假设共享同一个底层的 LLM 客户端
state_manager = StateManager()


# dialogue_logger = DialogueLogger() # 已经在 logger.py 中实例化了 logger_instance

# ==========================================
# 数据模型
# ==========================================
class DialogueRequest(BaseModel):
    npc_id: str = Field(..., description="目标NPC的ID")
    player_name: str = Field(..., description="玩家的名称")
    player_message: str = Field(..., description="玩家发送的聊天内容")


class DialogueResponse(BaseModel):
    npc_reply: str = Field(..., description="NPC的回复文本")
    affinity_level: str = Field(..., description="当前好感度等级")
    affinity_score: int = Field(..., description="当前好感度具体分数")


# ==========================================
# 生命周期事件
# ==========================================
@app.on_event("startup")
async def startup_event():
    """应用启动时的初始化"""
    print("=" * 60)
    print("🎮 赛博小镇后端服务启动中...")
    print("=" * 60)

    # 1. 必须先初始化 StateManager，生成张三、李四的默认基础状态（花名册）
    state_manager.initialize_npcs()

    # 2. 拿到花名册，转换成列表
    all_npcs_data = state_manager.get_all_npc_states()

    # 3. 让 AgentManager 根据花名册批量制造大脑
    agent_manager.initialize_npcs(all_npcs_data)

    print("✅ NPC及状态管理器 已初始化完毕")

    # 启动小镇后台时间齿轮
    asyncio.create_task(background_dialogue_update())
    print(f"✅ 后台时间齿轮 已挂载 (每 {settings.town_tick_seconds} 秒心跳)")


# ==========================================
# 核心接口 1：即时交互端点 (玩家 -> NPC)
# ==========================================
@app.post("/dialogue", response_model=DialogueResponse)
async def dialogue(request: DialogueRequest, background_tasks: BackgroundTasks):
    # 1. 验证 NPC 是否存在
    npc_state = state_manager.get_npc_state(request.npc_id)
    if not npc_state:
        raise HTTPException(status_code=404, detail=f"找不到 NPC: {request.npc_id}")

    # 2. 并发控制：检查并设置 NPC 忙碌状态 (上锁)
    if state_manager.is_npc_busy(request.npc_id):
        raise HTTPException(status_code=409, detail=f"NPC {npc_state['name']} 正在思考或与他人交谈，请稍后再试")

    state_manager.set_npc_busy(request.npc_id, True)

    try:
        # 3. 获取 NPC 的 Agent 实例
        target_agent = agent_manager.get_agent(request.npc_id)
        if not target_agent:
            raise HTTPException(status_code=500, detail="NPC 大脑(Agent)未初始化")

        # 4. 获取当前好感度信息
        affinity_info = relationship_manager.get_affinity_info(request.npc_id, request.player_name)
        current_level = affinity_info.get("level", "陌生")

        # 5. 🚀 动态态度注入 (核心魔法) 🚀
        attitude_prompt = {
            "陌生": "保持礼貌但有距离感，话不要太多。",
            "熟悉": "像对待普通同事一样，可以多聊几句。",
            "友好": "态度热情，愿意主动分享一些自己的生活趣事。",
            "亲密": "非常开心，语气中带有关心，甚至可以用一些昵称。",
            "挚友": "无话不谈，极其护短，可以开些只有好朋友之间才开的玩笑。"
        }

        system_instruction = f"""你是{npc_state['name']}，职业是{npc_state['role']}。
现在正在和你对话的是玩家【{request.player_name}】。
你对他当前的好感度等级是：【{current_level}】。
请严格遵循以下态度来回复他：{attitude_prompt.get(current_level, "保持中立")}
"""

        # 6. 调用大模型生成对话 (假设 target_agent 有 chat 接口)
        reply = await target_agent.chat(
            player_name=request.player_name,
            player_message=request.player_message,
            system_prompt=system_instruction
        )

        # 7. 对话结束后，异步分析并更新好感度
        new_affinity = await relationship_manager.update_affinity(
            request.npc_id, request.player_name, request.player_message, reply
        )

        # 8. 将写日志的动作“扔”给 FastAPI 后台任务 (无阻塞返回)
        background_tasks.add_task(
            logger_instance.log_dialogue,
            npc_id=request.npc_id,
            player_name=request.player_name,
            player_message=request.player_message,
            npc_reply=reply,
            affinity_info=new_affinity
        )

        # 9. 瞬间返回给前端
        return DialogueResponse(
            npc_reply=reply,
            affinity_level=new_affinity["level"],
            affinity_score=new_affinity["score"]
        )


    except Exception as e:

        # 🚀 架构师补丁：把真正的案发现场打印到终端！
        print(f"🚨 /dialogue 接口内部发生致命错误：{e}")
        traceback.print_exc()

        background_tasks.add_task(logger_instance.log_error, f"对话处理失败 ({request.npc_id}): {str(e)}")
        raise HTTPException(status_code=500, detail="对话生成异常: 请稍后再试")

    finally:
        # 10. 无论成功还是报错，必定释放 NPC 的锁！
        state_manager.set_npc_busy(request.npc_id, False)


# ==========================================
# 状态同步接口 (Godot 前端拉取专用)
# ==========================================
@app.get("/npcs/status")
async def get_npc_status():
    """获取全局所有 NPC 的状态"""
    npcs = state_manager.get_all_npc_states()
    return {"npcs": npcs}


@app.get("/npcs/{npc_id}/status")
async def get_single_npc_status(npc_id: str):
    """获取单个指定 NPC 的详细状态"""
    npc_state = state_manager.get_npc_state(npc_id)
    if not npc_state:
        raise HTTPException(status_code=404, detail=f"NPC [{npc_id}] 状态不存在")
    return npc_state


# ==========================================
# 核心接口 2：后台沙盒推演 (小镇物理运转)
# ==========================================
# ==========================================
# 核心接口 2：后台沙盒推演 (小镇物理运转)
# ==========================================
async def background_dialogue_update():
    """后台任务: 定期批量生成背景对话/动作 (赛博小镇的时间齿轮)"""
    while True:
        try:
            # 休息指定的心跳时间
            await asyncio.sleep(settings.town_tick_seconds)

            dialogues = {}
            all_npcs = state_manager.get_all_npc_states()

            # 🚀 修复点：将 .items() 改为列表遍历，并手动提取 npc_id
            for state in all_npcs:
                npc_id = state.get("npc_id")
                if not npc_id:
                    continue

                # 如果 NPC 正在和玩家聊天，跳过他的后台动作生成
                if state.get("is_busy"):
                    continue

                target_agent = agent_manager.get_agent(npc_id)
                if target_agent:
                    # 假装调用大模型，根据他目前的职业和状态生成一句话
                    # 实际开发中这里可以是一个开销较小的小模型 API 调用
                    action_text = f"（独自一人发呆，整理了一下衣服）"
                    dialogues[npc_id] = action_text

            # 批量更新到 StateManager 并塞入 NPC 的个人记忆
            for npc_id, action_text in dialogues.items():
                state_manager.update_npc_background_dialogue(npc_id, action_text)
                target_agent = agent_manager.get_agent(npc_id)
                if target_agent and hasattr(target_agent, 'memory'):
                    await target_agent.memory.add_interaction(
                        role="assistant", content=f"[背景动作]: {action_text}"
                    )
        except Exception as e:
            print(f"❌ 背景演算时间齿轮异常: {e}")


# ==========================================
# 基础运维接口
# ==========================================
@app.get("/")
async def root():
    """健康检查"""
    return {
        "status": "running",
        "message": "赛博小镇后端服务正在运行",
        "version": "1.0.0",
        "npcs_online": state_manager.get_npc_count()
    }


if __name__ == "__main__":
    # 使用 config.py 里定义的变量，假设你有定义
    uvicorn.run(
        "main:app",
        host=getattr(settings, "HOST", "0.0.0.0"),
        port=getattr(settings, "PORT", 8000),
        log_level="info",
        reload=True
    )