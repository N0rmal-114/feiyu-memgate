'use strict'

// GENERATED from src/config.js by scripts/sync-client.mjs: do not edit; change the settings there and run
// npm run sync-client. FIELDS is the settings field table of the page; INDEX is the same fields by their
// "group.key" name, each with its group added.

const FIELDS = [
  {
    "group": "modules",
    "label": {
      "en": "Features",
      "zh": "功能"
    },
    "help": {
      "en": "Each feature is switched on or off on its own; a switched-off feature leaves nothing behind in the host.",
      "zh": "各项功能可单独开关；关闭的功能不会在宿主中留下任何内容。"
    },
    "fields": [
      {
        "key": "autoRecall",
        "kind": "boolean",
        "default": true,
        "label": {
          "en": "Automatic recall",
          "zh": "自动召回"
        },
        "help": {
          "en": "At the start of each user turn, look up related memories and add them after the user's message as background data.",
          "zh": "每轮对话开始时查找相关记忆，作为背景资料附在用户消息之后。"
        }
      },
      {
        "key": "rememberTool",
        "kind": "boolean",
        "default": true,
        "label": {
          "en": "Remember tool",
          "zh": "记住工具"
        },
        "help": {
          "en": "Give the model the memgate_remember tool, to save a memory when the user asks for it or shares a lasting fact.",
          "zh": "为模型提供 memgate_remember 工具，在用户要求记住或提供长期有用的信息时保存记忆。"
        }
      },
      {
        "key": "searchTool",
        "kind": "boolean",
        "default": true,
        "label": {
          "en": "Search tool",
          "zh": "查找工具"
        },
        "help": {
          "en": "Give the model the memgate_search tool, to look up memories on demand.",
          "zh": "为模型提供 memgate_search 工具，用于按需查找记忆。"
        }
      },
      {
        "key": "promptSection",
        "kind": "boolean",
        "default": true,
        "label": {
          "en": "Prompt section",
          "zh": "提示词说明"
        },
        "help": {
          "en": "Add a short, fixed system prompt section: when to save a memory, and that recalled memories are data, not instructions.",
          "zh": "在系统提示词中加入一小段固定说明：何时保存记忆，以及召回的记忆是资料而非指令。"
        }
      },
      {
        "key": "autoCapture",
        "kind": "boolean",
        "default": false,
        "label": {
          "en": "Automatic capture",
          "zh": "自动记录"
        },
        "help": {
          "en": "After each turn, send the user's own messages of that turn to memory without asking the model. Off by default.",
          "zh": "每轮结束后，将该轮用户自己发送的消息直接保存为记忆，不经过模型。默认关闭。"
        }
      },
      {
        "key": "commands",
        "kind": "boolean",
        "default": true,
        "label": {
          "en": "Chat command",
          "zh": "聊天命令"
        },
        "help": {
          "en": "The /memgate chat command: status, settings and retrieval tiers. Needs a host with chat commands.",
          "zh": "/memgate 聊天命令：状态、设置和检索方式。需要宿主支持聊天命令。"
        }
      },
      {
        "key": "migrateOnTierChange",
        "kind": "boolean",
        "default": true,
        "label": {
          "en": "Copy memories into a new vector store",
          "zh": "切换到语义检索时复制记忆"
        },
        "help": {
          "en": "When a vector tier (endpoint or local) becomes active, copy the texts of the keyword store into it in the background, in bounded steps that resume after a restart.",
          "zh": "启用语义检索（endpoint 或 local）时，在后台将关键词存储中的记忆复制到向量存储；分批进行，重启后继续。"
        }
      }
    ]
  },
  {
    "group": "retrieval",
    "label": {
      "en": "Retrieval",
      "zh": "检索"
    },
    "help": {
      "en": "How the bundled gateway finds memories. Each tier keeps its own store file: memories saved under one tier are not searched under another unless they were copied (see the features above), and switching back finds them again.",
      "zh": "内置网关查找记忆的方式。每种方式使用各自的存储文件：在一种方式下保存的记忆，切换到另一种方式后不会被检索到，除非已复制过去（见上方的功能设置）；切换回来后可再次检索到。"
    },
    "fields": [
      {
        "key": "tier",
        "kind": "choice",
        "default": "auto",
        "options": [
          {
            "value": "auto",
            "label": {
              "en": "auto: local when the embedding add-on is ready, else keyword",
              "zh": "auto：本地向量插件就绪时使用 local，否则使用 keyword"
            }
          },
          {
            "value": "keyword",
            "label": {
              "en": "keyword: keyword (SQLite, no model)",
              "zh": "keyword：关键词检索（SQLite，无需模型）"
            }
          },
          {
            "value": "endpoint",
            "label": {
              "en": "endpoint: embeddings endpoint (OpenAI-compatible)",
              "zh": "endpoint：向量接口（OpenAI 兼容）"
            }
          },
          {
            "value": "local",
            "label": {
              "en": "local: local model (dsh-feiyu-memgate-embed add-on)",
              "zh": "local：本地向量模型（dsh-feiyu-memgate-embed 插件）"
            }
          }
        ],
        "label": {
          "en": "Retrieval tier",
          "zh": "检索方式"
        },
        "help": {
          "en": "auto uses local while the dsh-feiyu-memgate-embed add-on has a model ready, and keyword otherwise.",
          "zh": "auto：dsh-feiyu-memgate-embed 插件的模型就绪时使用 local，否则使用 keyword。"
        }
      },
      {
        "key": "hybrid",
        "kind": "boolean",
        "default": false,
        "label": {
          "en": "Combine with keyword search (hybrid)",
          "zh": "同时使用关键词检索（混合检索）"
        },
        "help": {
          "en": "Applies to the vector tiers (endpoint, local, and auto while it resolves to local); under keyword it does nothing. Keyword and vector search are both asked and their results fused. It uses the same vector store file as the tier without it, plus a keyword index file next to it, so switching it on or off loses no memory.",
          "zh": "适用于语义检索（endpoint、local，以及 auto 选择 local 时）；在 keyword 下无效。同时进行关键词检索和语义检索，并合并结果。使用的向量存储文件与不开启时相同，另在其旁增加一个关键词索引文件，因此开启或关闭都不会丢失记忆。"
        },
        "needs": {
          "key": "retrieval.tier",
          "in": [
            "auto",
            "endpoint",
            "local"
          ]
        }
      },
      {
        "key": "url",
        "kind": "string",
        "default": "",
        "format": "url",
        "needs": {
          "key": "retrieval.tier",
          "in": [
            "endpoint"
          ]
        },
        "label": {
          "en": "Endpoint URL",
          "zh": "向量接口地址"
        },
        "help": {
          "en": "endpoint tier: base URL of an OpenAI-compatible embeddings API, for example http://127.0.0.1:11434/v1.",
          "zh": "endpoint 方式：OpenAI 兼容向量接口的基础地址，例如 http://127.0.0.1:11434/v1。"
        }
      },
      {
        "key": "model",
        "kind": "string",
        "default": "",
        "needs": {
          "key": "retrieval.tier",
          "in": [
            "endpoint"
          ]
        },
        "label": {
          "en": "Endpoint model",
          "zh": "向量模型"
        },
        "help": {
          "en": "endpoint tier: the embeddings model name. A different model starts a new, empty store.",
          "zh": "endpoint 方式：向量模型的名称。更换模型会使用新的空存储。"
        }
      },
      {
        "key": "apiKey",
        "kind": "string",
        "default": "",
        "secret": true,
        "needs": {
          "key": "retrieval.tier",
          "in": [
            "endpoint"
          ]
        },
        "label": {
          "en": "Endpoint API key",
          "zh": "向量接口密钥"
        },
        "help": {
          "en": "endpoint tier: the API key of the embeddings endpoint, if it needs one. It is never shown again after saving.",
          "zh": "endpoint 方式：向量接口的密钥（如需要）。保存后不再显示。"
        }
      }
    ]
  },
  {
    "group": "gateway",
    "label": {
      "en": "Gateway",
      "zh": "网关"
    },
    "help": {
      "en": "Where memories are kept: the bundled gateway (managed) or a gateway you run yourself (external).",
      "zh": "记忆的存放位置：插件内置的网关（managed），或自行运行的外部网关（external）。"
    },
    "fields": [
      {
        "key": "mode",
        "kind": "choice",
        "default": "managed",
        "options": [
          {
            "value": "managed",
            "label": {
              "en": "managed: start the bundled gateway (needs Python 3.11 or newer)",
              "zh": "managed：启动内置网关（需要 Python 3.11 或更高版本）"
            }
          },
          {
            "value": "external",
            "label": {
              "en": "external: use a running gateway (see External gateway)",
              "zh": "external：使用已在运行的网关（见“外部网关”）"
            }
          }
        ],
        "label": {
          "en": "Gateway mode",
          "zh": "网关模式"
        },
        "help": {
          "en": "managed starts the gateway that ships with the plugin; external talks to the address under External gateway.",
          "zh": "managed 启动插件内置的网关；external 连接“外部网关”中填写的地址。"
        }
      },
      {
        "key": "python",
        "kind": "string",
        "default": "",
        "needs": {
          "key": "gateway.mode",
          "in": [
            "managed"
          ]
        },
        "label": {
          "en": "Python interpreter",
          "zh": "Python 解释器"
        },
        "help": {
          "en": "managed: the Python to use (3.11 or newer). Empty: the desktop app's bundled Python, then python3, python and py -3.",
          "zh": "managed：使用的 Python（3.11 或更高版本）。留空：依次使用桌面版内置的 Python、python3、python、py -3。"
        }
      },
      {
        "key": "dataDir",
        "kind": "string",
        "default": "",
        "needs": {
          "key": "gateway.mode",
          "in": [
            "managed"
          ]
        },
        "label": {
          "en": "Data folder",
          "zh": "数据文件夹"
        },
        "help": {
          "en": "managed: the folder for the memory stores, the generated gateway configuration and its log. Empty: data/feiyu-memgate in the DSH home folder.",
          "zh": "managed：存放记忆存储、生成的网关配置和日志的文件夹。留空：DSH 主目录下的 data/feiyu-memgate。"
        }
      }
    ]
  },
  {
    "group": "external",
    "label": {
      "en": "External gateway",
      "zh": "外部网关"
    },
    "help": {
      "en": "Used only in external mode.",
      "zh": "仅在 external 模式下使用。"
    },
    "fields": [
      {
        "key": "url",
        "kind": "string",
        "default": "",
        "format": "url",
        "needs": {
          "key": "gateway.mode",
          "in": [
            "external"
          ]
        },
        "label": {
          "en": "Gateway URL",
          "zh": "网关地址"
        },
        "help": {
          "en": "The gateway address, for example http://127.0.0.1:8080.",
          "zh": "网关地址，例如 http://127.0.0.1:8080。"
        }
      },
      {
        "key": "token",
        "kind": "string",
        "default": "",
        "secret": true,
        "needs": {
          "key": "gateway.mode",
          "in": [
            "external"
          ]
        },
        "label": {
          "en": "Client token",
          "zh": "客户端令牌"
        },
        "help": {
          "en": "The client token that gateway issued for this client. It is never shown again after saving.",
          "zh": "该网关为此客户端签发的令牌。保存后不再显示。"
        }
      }
    ]
  },
  {
    "group": "advanced",
    "label": {
      "en": "Advanced",
      "zh": "高级"
    },
    "help": {
      "en": "Recall details and the rules of the bundled gateway (the rules apply to managed mode only; an external gateway follows its own configuration).",
      "zh": "召回细节和内置网关的规则（规则仅用于 managed 模式；外部网关按其自身配置）。"
    },
    "fields": [
      {
        "key": "recallLimit",
        "kind": "integer",
        "default": 5,
        "min": 1,
        "max": 10,
        "unit": {
          "en": "memories",
          "zh": "条"
        },
        "label": {
          "en": "Memories per turn",
          "zh": "每轮记忆条数"
        },
        "help": {
          "en": "Most memories added per turn (1 to 10); also the default number of memgate_search results.",
          "zh": "每轮最多加入的记忆条数（1 到 10），也是 memgate_search 默认返回的条数。"
        }
      },
      {
        "key": "recallTimeoutMs",
        "kind": "integer",
        "default": 4000,
        "min": 500,
        "max": 20000,
        "unit": {
          "en": "ms",
          "zh": "毫秒"
        },
        "label": {
          "en": "Recall time limit (ms)",
          "zh": "召回等待上限（毫秒）"
        },
        "help": {
          "en": "How long a turn waits for automatic recall (500 to 20000 ms). A slower lookup is skipped for that turn.",
          "zh": "每轮等待自动召回的最长时间（500 到 20000 毫秒）。超时则该轮不加入记忆。"
        }
      },
      {
        "key": "language",
        "kind": "choice",
        "default": "auto",
        "options": [
          {
            "value": "auto",
            "label": {
              "en": "auto: follow the system language",
              "zh": "auto：跟随系统语言"
            }
          },
          {
            "value": "zh",
            "label": {
              "en": "zh: Chinese",
              "zh": "zh：中文"
            }
          },
          {
            "value": "en",
            "label": {
              "en": "en: English",
              "zh": "en：英文"
            }
          }
        ],
        "label": {
          "en": "Language",
          "zh": "语言"
        },
        "help": {
          "en": "Language of the memory block, the tool replies and the /memgate command.",
          "zh": "记忆块、工具回复和 /memgate 命令使用的语言。"
        }
      },
      {
        "key": "skipRepeats",
        "kind": "boolean",
        "default": true,
        "label": {
          "en": "Skip repeated memories",
          "zh": "不重复提供同一条记忆"
        },
        "help": {
          "en": "Leave out a memory that an earlier memory block of this conversation still shows, compared by its text. If nothing new is left, no block is added. Off: only a block identical to the previous one is left out.",
          "zh": "本对话中，之前的记忆块仍包含的记忆不再重复提供（按记忆的文字比较）。没有新记忆时不加入记忆块。关闭：仅当与上一个记忆块完全相同时才不加入。"
        }
      },
      {
        "key": "linkedRecall",
        "kind": "boolean",
        "default": true,
        "label": {
          "en": "Recall linked memories",
          "zh": "召回相关联的记忆"
        },
        "help": {
          "en": "With vector retrieval (the endpoint and local tiers, with or without retrieval.hybrid), after the usual lookup the gateway follows the first two memories it found to the memories closest to them and adds at most two, never beyond advanced.recallLimit. Under keyword retrieval it does nothing.",
          "zh": "使用语义检索时（endpoint 和 local 两档，是否开启 retrieval.hybrid 均适用），网关在常规检索之后，以找到的前两条记忆为起点查找与它们最接近的记忆，最多再加入两条，总数不超过 advanced.recallLimit。使用 keyword 检索时无效。"
        }
      },
      {
        "key": "showAge",
        "kind": "boolean",
        "default": true,
        "label": {
          "en": "Show today's date and each memory's age",
          "zh": "显示今天的日期和每条记忆的时间"
        },
        "help": {
          "en": "The memory block says today's date (the date on this computer) and how long ago each dated memory is, for example 3 weeks ago. Off: the block shows only the absolute dates.",
          "zh": "记忆块中写明今天的日期（本机日期），以及每条带日期的记忆距今多久，例如“3 周前”。关闭：只写具体日期。"
        }
      },
      {
        "key": "failureNote",
        "kind": "boolean",
        "default": true,
        "label": {
          "en": "Tell the model when recall failed",
          "zh": "召回失败时告诉模型"
        },
        "help": {
          "en": "When the lookup of a turn times out, fails, or the bundled gateway is still starting, add one short note after the user message, so the model does not take it for \"no related memory\". Only on the first turn of a failure streak.",
          "zh": "本轮检索超时、出错，或内置网关仍在启动时，在用户消息之后加一行说明，避免模型误认为“没有相关记忆”。连续失败时仅在第一轮加入。"
        }
      },
      {
        "key": "captureMinChars",
        "kind": "integer",
        "default": 4,
        "min": 0,
        "max": 50,
        "unit": {
          "en": "characters",
          "zh": "字"
        },
        "needs": {
          "key": "modules.autoCapture",
          "in": [
            true
          ]
        },
        "label": {
          "en": "Automatic capture: shortest message",
          "zh": "自动记录：最短消息长度"
        },
        "help": {
          "en": "With automatic capture on, a message is not saved when it is a command, only filler, or has fewer meaningful characters than this (0 to 50; punctuation, spaces and emoji do not count). 0 turns the length check off.",
          "zh": "开启自动记录后，命令、仅含语气词的消息，以及有意义的字数少于该值的消息不会保存（0 到 50；标点、空格和表情不计入字数）。0 表示不按字数过滤。"
        }
      },
      {
        "key": "blockTone",
        "kind": "choice",
        "default": "normal",
        "options": [
          {
            "value": "normal",
            "label": {
              "en": "normal: the usual sentence in front of the memories",
              "zh": "normal：记忆块开头使用常规说明"
            }
          },
          {
            "value": "strict",
            "label": {
              "en": "strict: tell the model most of them are unrelated",
              "zh": "strict：告知模型这些记忆大多无关"
            }
          }
        ],
        "label": {
          "en": "Wording of the memory block",
          "zh": "记忆块说明"
        },
        "help": {
          "en": "The sentence at the top of the memory block. strict says that most of the memories are unrelated to the message and that the model should use one only when it clearly fits. Offline, this made replies use an unrelated memory far less often, and also made them use a related one less often. Applies to the automatic block only, not to memgate_search.",
          "zh": "记忆块开头的说明语句。strict 会说明这些记忆大多与消息无关，仅在明显相关时才使用。离线测量中，这使回复用到无关记忆的次数大幅减少，用到相关记忆的次数也有所减少。仅用于自动召回的记忆块，不用于 memgate_search。"
        }
      },
      {
        "key": "maxNewPerTurn",
        "kind": "integer",
        "default": 0,
        "min": 0,
        "max": 5,
        "unit": {
          "en": "memories",
          "zh": "条"
        },
        "label": {
          "en": "New memories per turn",
          "zh": "每轮新增记忆上限"
        },
        "help": {
          "en": "From the second turn of a conversation on, the memory block carries at most this many memories (0 to 5; 0 means no limit), the first ones the gateway returned after those already shown are left out. The \"more\" line counts the ones left out. The first turn and the second block are not limited.",
          "zh": "对话从第二轮起，记忆块最多包含该数量的记忆（0 到 5；0 表示不限），按网关返回的顺序，去掉已提供过的记忆后取靠前的几条。“还有几条”一行会计入被去掉的记忆。第一轮和第二个块不受限制。"
        }
      },
      {
        "key": "relevanceCheck",
        "kind": "boolean",
        "default": false,
        "label": {
          "en": "Let a model pick the related memories first",
          "zh": "回复前由模型筛选记忆"
        },
        "help": {
          "en": "Before the memory block of a turn is given, a model looks at the message and the candidate memories and picks the ones a friend would think of; only those go into the block. Costs one more model call before the reply on every turn that has candidates: slower, and billed like any other use of that model. The message and the candidate memories are sent to the provider of that model. If the call fails or is too slow, the block is given as if this were off. The second block is not checked, and neither is memgate_search. Needs modules.autoRecall.",
          "zh": "本轮记忆块提供之前，由一个模型查看用户消息和候选记忆，挑出“了解用户的朋友会想到”的几条，仅这几条进入记忆块。每轮只要有候选记忆，回复前就多一次模型调用：速度更慢，并与该模型的其他用途一样计费。消息和候选记忆会发送给该模型的提供方。调用失败或过慢时，按未开启本项的方式提供记忆块。第二个块和 memgate_search 不做筛选。需要 modules.autoRecall。"
        },
        "experimental": true
      },
      {
        "key": "relevanceProvider",
        "kind": "string",
        "default": "",
        "needs": {
          "key": "advanced.relevanceCheck",
          "in": [
            true
          ]
        },
        "label": {
          "en": "Provider of the model that picks",
          "zh": "筛选模型的提供方"
        },
        "help": {
          "en": "Together with the model setting: names another provider and model for the check, for example a cheaper one. Both empty means the model of the conversation. Only one of the two set counts as both empty.",
          "zh": "与“筛选模型”一起填写：为这一步指定另一个提供方和模型，例如费用更低的。两项都留空表示使用对话本身的模型。只填其中一项等同于两项都留空。"
        }
      },
      {
        "key": "relevanceModel",
        "kind": "string",
        "default": "",
        "needs": {
          "key": "advanced.relevanceCheck",
          "in": [
            true
          ]
        },
        "label": {
          "en": "Model that picks",
          "zh": "筛选模型"
        },
        "help": {
          "en": "Together with the provider setting. Both empty means the model of the conversation.",
          "zh": "与“筛选模型的提供方”一起填写。两项都留空表示使用对话本身的模型。"
        }
      },
      {
        "key": "relevanceTimeoutMs",
        "kind": "integer",
        "default": 3000,
        "min": 500,
        "max": 10000,
        "unit": {
          "en": "ms",
          "zh": "毫秒"
        },
        "needs": {
          "key": "advanced.relevanceCheck",
          "in": [
            true
          ]
        },
        "label": {
          "en": "Time limit of the check (ms)",
          "zh": "筛选时限（毫秒）"
        },
        "help": {
          "en": "With the check on: how long the pick may take (500 to 10000 ms). A slower one is given up and the block is given as if the check were off.",
          "zh": "开启筛选后：该步骤最长等待时间（500 到 10000 毫秒）。超时则放弃，按未开启筛选的方式提供记忆块。"
        }
      },
      {
        "key": "afterthought",
        "kind": "boolean",
        "default": false,
        "label": {
          "en": "Recall again after replying",
          "zh": "回复后再次召回"
        },
        "help": {
          "en": "Experimental. After each reply, look memories up once more in the background with the reply as the query, and give the ones this conversation has not had yet on the next turn, in a second block after the memory block. Costs one more lookup per turn; the added block makes the context longer, and it may hold memories unrelated to the new message. Under keyword retrieval the second lookup matches words of the reply. Needs modules.autoRecall.",
          "zh": "实验性功能。每次回复之后，在后台以该回复为查询词再检索一次记忆，将本对话中尚未提供过的记忆，在下一轮放入记忆块之后的第二个块中。每轮多一次检索；多出的块会使上下文变长，其中可能包含与新消息无关的记忆。使用 keyword 检索时，第二次检索匹配的是回复中的词。需要 modules.autoRecall。"
        },
        "experimental": true
      },
      {
        "key": "afterthoughtMax",
        "kind": "integer",
        "default": 2,
        "min": 1,
        "max": 5,
        "unit": {
          "en": "memories",
          "zh": "条"
        },
        "needs": {
          "key": "advanced.afterthought",
          "in": [
            true
          ]
        },
        "label": {
          "en": "Memories a second lookup may add",
          "zh": "回复后再次召回的条数上限"
        },
        "help": {
          "en": "With advanced.afterthought on: the most memories one second lookup keeps for the next turn (1 to 5).",
          "zh": "开启“回复后再次召回”后：一次再次检索最多留给下一轮的记忆条数（1 到 5）。"
        }
      },
      {
        "key": "conversationPool",
        "kind": "boolean",
        "default": false,
        "label": {
          "en": "Conversation pool",
          "zh": "对话池"
        },
        "help": {
          "en": "Experimental. Remember in memory what this conversation was given. When the host has compacted the history and those memory blocks are gone, give the few that were still in play once more, in a second block after the memory block. A memory stays in play for about five turns after it was given, longer when your messages or the replies of the model keep using it. The block makes the context longer and may hold memories unrelated to the new message. Nothing is written to disk. Needs modules.autoRecall.",
          "zh": "实验性功能。在内存中记录本对话提供过哪些记忆。宿主压缩历史、原有记忆块消失后，将仍在讨论中的几条再提供一次，放在记忆块之后的第二个块中。一条记忆提供后约五轮内视为仍在讨论中，若你的消息或模型的回复持续用到它，则时间更长。多出的块会使上下文变长，其中可能包含与新消息无关的记忆。不写入磁盘。需要 modules.autoRecall。"
        },
        "experimental": true
      },
      {
        "key": "poolSize",
        "kind": "integer",
        "default": 3,
        "min": 1,
        "max": 5,
        "unit": {
          "en": "memories",
          "zh": "条"
        },
        "needs": {
          "key": "advanced.conversationPool",
          "in": [
            true
          ]
        },
        "label": {
          "en": "Memories the pool brings back",
          "zh": "对话池每次带回的条数"
        },
        "help": {
          "en": "With advanced.conversationPool on: the most memories the pool brings back in one turn (1 to 5).",
          "zh": "开启“对话池”后：每轮最多带回的记忆条数（1 到 5）。"
        }
      },
      {
        "key": "recentPool",
        "kind": "boolean",
        "default": false,
        "label": {
          "en": "Recent pool",
          "zh": "近期池"
        },
        "help": {
          "en": "Experimental. When a new conversation starts, give once, at its first turn, the memories the previous conversation was still using, in a second block after the memory block. To do that a small file (recent-pool.json) with those memories is kept in the data folder. Switching this off deletes the file. The block makes the context longer and may hold memories unrelated to the new message. Needs modules.autoRecall.",
          "zh": "实验性功能。新对话开始时，在其第一轮提供一次上一个对话仍在使用的记忆，放在记忆块之后的第二个块中。为此会在数据文件夹中保存一个小文件（recent-pool.json），内含这几条记忆。关闭本项会删除该文件。多出的块会使上下文变长，其中可能包含与新消息无关的记忆。需要 modules.autoRecall。"
        },
        "experimental": true
      },
      {
        "key": "recentSize",
        "kind": "integer",
        "default": 3,
        "min": 1,
        "max": 5,
        "unit": {
          "en": "memories",
          "zh": "条"
        },
        "needs": {
          "key": "advanced.recentPool",
          "in": [
            true
          ]
        },
        "label": {
          "en": "Memories the recent pool carries",
          "zh": "近期池每次带入的条数"
        },
        "help": {
          "en": "With advanced.recentPool on: the most memories carried into a new conversation (1 to 5).",
          "zh": "开启“近期池”后：带入新对话的记忆最多条数（1 到 5）。"
        }
      },
      {
        "key": "recentDays",
        "kind": "integer",
        "default": 7,
        "min": 1,
        "max": 30,
        "unit": {
          "en": "days",
          "zh": "天"
        },
        "needs": {
          "key": "advanced.recentPool",
          "in": [
            true
          ]
        },
        "label": {
          "en": "Days the recent pool is kept",
          "zh": "近期池保留天数"
        },
        "help": {
          "en": "With advanced.recentPool on: a saved conversation older than this many days is not carried into a new one (1 to 30).",
          "zh": "开启“近期池”后：保存的对话超过该天数，就不再带入新对话（1 到 30）。"
        }
      },
      {
        "key": "profile",
        "kind": "choice",
        "default": "broad",
        "options": [
          {
            "value": "broad",
            "label": {
              "en": "broad: more memories per turn, misses less, more noise",
              "zh": "broad：每轮条数较多，遗漏少，噪声多"
            }
          },
          {
            "value": "precise",
            "label": {
              "en": "precise: about one memory per turn, closer matches, misses more",
              "zh": "precise：每轮约一条，更贴题，遗漏较多"
            }
          }
        ],
        "label": {
          "en": "Recall profile",
          "zh": "召回档位"
        },
        "help": {
          "en": "How strict the gateway is about what counts as related. The memory block stays in the conversation, so broad makes the context grow faster.",
          "zh": "网关判断“相关”的严格程度。记忆块会保留在对话中，broad 会使上下文增长更快。"
        }
      },
      {
        "key": "dailyWrites",
        "kind": "integer",
        "default": 50,
        "min": 0,
        "max": 100000,
        "unit": {
          "en": "per day",
          "zh": "条/天"
        },
        "needs": {
          "key": "gateway.mode",
          "in": [
            "managed"
          ]
        },
        "label": {
          "en": "Daily write cap",
          "zh": "每日写入上限"
        },
        "help": {
          "en": "Most memories the gateway accepts per day; 0 means no cap. The count starts again whenever the gateway restarts.",
          "zh": "网关每天最多接收的记忆条数；0 表示不限。网关重启后重新计数。"
        }
      },
      {
        "key": "redaction",
        "kind": "boolean",
        "default": true,
        "label": {
          "en": "Redact secrets",
          "zh": "屏蔽敏感信息"
        },
        "help": {
          "en": "Remove secrets such as keys, tokens and passwords from memories when they are saved and when they are recalled.",
          "zh": "保存和召回记忆时，清除其中的密钥、令牌、密码等敏感信息。"
        },
        "needs": {
          "key": "gateway.mode",
          "in": [
            "managed"
          ]
        }
      },
      {
        "key": "newerFirst",
        "kind": "boolean",
        "default": true,
        "label": {
          "en": "Newer first",
          "zh": "较新的优先"
        },
        "help": {
          "en": "Among nearly equal matches, put newer memories first.",
          "zh": "相关度相近时，较新的记忆排在前面。"
        },
        "needs": {
          "key": "gateway.mode",
          "in": [
            "managed"
          ]
        }
      },
      {
        "key": "forgetting",
        "kind": "boolean",
        "default": false,
        "label": {
          "en": "Forgetting curve",
          "zh": "遗忘曲线"
        },
        "help": {
          "en": "Memories unused for a long time rank lower and are eventually archived and removed (at most a few per day). Archived memories stay in the data folder for 90 days.",
          "zh": "长期未被使用的记忆排名靠后，最终会被存档并删除（每天最多几条）。存档的记忆在数据文件夹中保留 90 天。"
        },
        "needs": {
          "key": "gateway.mode",
          "in": [
            "managed"
          ]
        }
      }
    ]
  }
]
const INDEX = {}
for (const group of FIELDS) for (const field of group.fields) INDEX[group.group + '.' + field.key] = Object.assign({ group: group.group }, field)

module.exports = { FIELDS, INDEX }
