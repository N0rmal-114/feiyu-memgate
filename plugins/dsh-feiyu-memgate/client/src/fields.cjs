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
      "zh": "每项功能单独开关；关掉的功能不会在宿主里留下任何东西。"
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
          "zh": "每轮用户发言开始时查找相关记忆，作为背景资料放在用户消息之后。"
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
          "zh": "给模型 memgate_remember 工具：用户要求记住、或说出长期有用的信息时记下来。"
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
          "zh": "给模型 memgate_search 工具：需要时主动查找记忆。"
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
          "zh": "在系统提示词里加一小段固定说明：什么时候记，召回的记忆是资料不是指令。"
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
          "zh": "每轮结束后，把这一轮用户自己发的消息直接交给记忆库，不经过模型。默认关闭。"
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
          "zh": "/memgate 聊天命令：状态、设置和检索方式。需要支持聊天命令的宿主。"
        }
      },
      {
        "key": "migrateOnTierChange",
        "kind": "boolean",
        "default": true,
        "label": {
          "en": "Copy memories into a new vector store",
          "zh": "切换到向量检索时复制记忆"
        },
        "help": {
          "en": "When a vector tier (endpoint or local) becomes active, copy the texts of the keyword store into it in the background, in bounded steps that resume after a restart.",
          "zh": "启用向量检索（endpoint 或 local）时，在后台把关键词存储里的记忆复制过去；分批进行，重启后接着复制。"
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
      "zh": "自带网关怎么查找记忆。每种方式各用一个存储文件：在一种方式下记的内容，换成另一种方式后不会被查到，除非已经复制过去（见上面的功能开关）；切回来还能找到。"
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
              "zh": "auto：本地向量插件就绪时用 local，否则用 keyword"
            }
          },
          {
            "value": "keyword",
            "label": {
              "en": "keyword: keyword (SQLite, no model)",
              "zh": "keyword：关键词（SQLite，不需要模型）"
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
              "zh": "local：本地模型（dsh-feiyu-memgate-embed 插件）"
            }
          }
        ],
        "label": {
          "en": "Retrieval tier",
          "zh": "检索方式"
        },
        "help": {
          "en": "auto uses local while the dsh-feiyu-memgate-embed add-on has a model ready, and keyword otherwise.",
          "zh": "auto：dsh-feiyu-memgate-embed 插件的模型就绪时用 local，否则用 keyword。"
        }
      },
      {
        "key": "hybrid",
        "kind": "boolean",
        "default": false,
        "label": {
          "en": "Combine with keyword search (hybrid)",
          "zh": "同时用关键词检索（混合）"
        },
        "help": {
          "en": "Applies to the vector tiers (endpoint, local, and auto while it resolves to local); under keyword it does nothing. Keyword and vector search are both asked and their results fused. It uses the same vector store file as the tier without it, plus a keyword index file next to it, so switching it on or off loses no memory.",
          "zh": "用于向量检索（endpoint、local，以及 auto 选到 local 的时候）；在 keyword 下不起作用。关键词检索和向量检索同时查，结果合并。它用的向量存储文件和不开它时相同，旁边多一个关键词索引文件，所以开或关都不会丢记忆。"
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
          "zh": "endpoint 方式：向量模型的名字。换模型会用一个新的空存储。"
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
          "zh": "endpoint 方式：向量接口的密钥（需要的话）。保存后不再显示。"
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
      "zh": "记忆放在哪里：插件自带的网关（managed），或你自己运行的网关（external）。"
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
              "zh": "managed：启动自带的网关（需要 Python 3.11 或更新）"
            }
          },
          {
            "value": "external",
            "label": {
              "en": "external: use a running gateway (see External gateway)",
              "zh": "external：使用已经在运行的网关（见“外部网关”）"
            }
          }
        ],
        "label": {
          "en": "Gateway mode",
          "zh": "网关模式"
        },
        "help": {
          "en": "managed starts the gateway that ships with the plugin; external talks to the address under External gateway.",
          "zh": "managed 启动插件自带的网关；external 连接“外部网关”里填写的地址。"
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
          "zh": "managed：使用哪个 Python（3.11 或更新）。留空：先用桌面版自带的 Python，再找 python3、python、py -3。"
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
          "zh": "managed：存放记忆库、生成的网关配置和日志的文件夹。留空：DSH 主目录下的 data/feiyu-memgate。"
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
      "zh": "只在 external 模式下使用。"
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
          "zh": "那个网关给这个客户端的令牌。保存后不再显示。"
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
      "zh": "召回细节和自带网关的规则（规则只用于 managed 模式；外部网关按它自己的配置）。"
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
          "zh": "每轮最多加入几条记忆（1 到 10），也是 memgate_search 默认返回的条数。"
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
          "zh": "每轮最多等自动召回多少毫秒（500 到 20000）。超时的话这一轮不加记忆。"
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
          "zh": "记忆块、工具回复和 /memgate 命令用的语言。"
        }
      },
      {
        "key": "skipRepeats",
        "kind": "boolean",
        "default": true,
        "label": {
          "en": "Skip repeated memories",
          "zh": "不重复给同一条记忆"
        },
        "help": {
          "en": "Leave out a memory that an earlier memory block of this conversation still shows, compared by its text. If nothing new is left, no block is added. Off: only a block identical to the previous one is left out.",
          "zh": "这个对话里前面的记忆块还留着的记忆，不再给第二次（按记忆的文字比较）。没有新的就不加记忆块。关掉：只有和上一个记忆块完全相同时才不加。"
        }
      },
      {
        "key": "linkedRecall",
        "kind": "boolean",
        "default": true,
        "label": {
          "en": "Recall linked memories",
          "zh": "顺带想起相连的记忆"
        },
        "help": {
          "en": "With vector retrieval (the endpoint and local tiers, with or without retrieval.hybrid), after the usual lookup the gateway follows the first two memories it found to the memories closest to them and adds at most two, never beyond advanced.recallLimit. Under keyword retrieval it does nothing.",
          "zh": "用向量检索时（endpoint 和 local 两档，开不开 retrieval.hybrid 都一样），网关在平常的查找之后，顺着找到的前两条记忆去找和它们最接近的记忆，最多再加两条，总数不超过 advanced.recallLimit。用 keyword 检索时什么都不做。"
        }
      },
      {
        "key": "showAge",
        "kind": "boolean",
        "default": true,
        "label": {
          "en": "Show today's date and each memory's age",
          "zh": "写明今天的日期和每条记忆的时间"
        },
        "help": {
          "en": "The memory block says today's date (the date on this computer) and how long ago each dated memory is, for example 3 weeks ago. Off: the block shows only the absolute dates.",
          "zh": "记忆块里写明今天的日期（这台电脑上的日期），以及每条有日期的记忆是多久以前的事，例如“3 周前”。关掉：只写具体日期。"
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
          "zh": "这一轮的查询超时、出错，或者自带网关还在启动时，在用户消息之后加一行说明，免得模型当作“没有相关记忆”。连续失败时只在第一轮加。"
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
          "zh": "自动记录：最短的消息"
        },
        "help": {
          "en": "With automatic capture on, a message is not saved when it is a command, only filler, or has fewer meaningful characters than this (0 to 50; punctuation, spaces and emoji do not count). 0 turns the length check off.",
          "zh": "自动记录打开时，命令、只有语气词的消息，以及有意义的字数少于这个数的消息不保存（0 到 50；标点、空格和表情不算字数）。0 表示不按字数过滤。"
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
              "zh": "normal：记忆块前面照常的那句话"
            }
          },
          {
            "value": "strict",
            "label": {
              "en": "strict: tell the model most of them are unrelated",
              "zh": "strict：告诉模型这些记忆大多无关"
            }
          }
        ],
        "label": {
          "en": "Wording of the memory block",
          "zh": "记忆块的说明"
        },
        "help": {
          "en": "The sentence at the top of the memory block. strict says that most of the memories are unrelated to the message and that the model should use one only when it clearly fits. Offline, this made replies use an unrelated memory far less often, and also made them use a related one less often. Applies to the automatic block only, not to memgate_search.",
          "zh": "记忆块最上面的那句话。strict 会说明这些记忆大多和消息无关，只有明显有关的才用。离线测量里，这让回复用上无关记忆的次数少了很多，用上相关记忆的次数也少了一些。只用于自动召回的记忆块，不用于 memgate_search。"
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
          "zh": "每轮最多新给几条"
        },
        "help": {
          "en": "From the second turn of a conversation on, the memory block carries at most this many memories (0 to 5; 0 means no limit), the first ones the gateway returned after those already shown are left out. The \"more\" line counts the ones left out. The first turn and the second block are not limited.",
          "zh": "一个对话从第二轮起，记忆块最多带这么多条记忆（0 到 5；0 表示不限），取网关返回的顺序里，去掉已经给过的之后最前面的几条。“还有几条”那一行会算上被去掉的。第一轮和第二个块不受限制。"
        }
      },
      {
        "key": "relevanceCheck",
        "kind": "boolean",
        "default": false,
        "label": {
          "en": "Let a model pick the related memories first",
          "zh": "回复前让模型先挑一遍"
        },
        "help": {
          "en": "Before the memory block of a turn is given, a model looks at the message and the candidate memories and picks the ones a friend would think of; only those go into the block. Costs one more model call before the reply on every turn that has candidates: slower, and billed like any other use of that model. The message and the candidate memories are sent to the provider of that model. If the call fails or is too slow, the block is given as if this were off. The second block is not checked, and neither is memgate_search. Needs modules.autoRecall.",
          "zh": "这一轮的记忆块给出之前，让一个模型看一遍用户的消息和候选记忆，挑出“一个了解主人的朋友会想到”的几条，只有这几条进记忆块。每一轮只要有候选记忆，回复之前就多一次模型调用：更慢，也和这个模型的其他用法一样计费。消息和候选记忆会发给这个模型的提供方。调用失败或太慢时，照没打开这项那样给出记忆块。第二个块不检查，memgate_search 也不检查。需要 modules.autoRecall。"
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
          "zh": "挑选用的模型的提供方"
        },
        "help": {
          "en": "Together with the model setting: names another provider and model for the check, for example a cheaper one. Both empty means the model of the conversation. Only one of the two set counts as both empty.",
          "zh": "和“挑选用的模型”一起填：为这一步指定另一个提供方和模型，比如便宜一点的。两项都空表示用对话本身的模型。只填其中一项等于两项都空。"
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
          "zh": "挑选用的模型"
        },
        "help": {
          "en": "Together with the provider setting. Both empty means the model of the conversation.",
          "zh": "和“挑选用的模型的提供方”一起填。两项都空表示用对话本身的模型。"
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
          "zh": "挑选的时限（毫秒）"
        },
        "help": {
          "en": "With the check on: how long the pick may take (500 to 10000 ms). A slower one is given up and the block is given as if the check were off.",
          "zh": "打开挑选之后：这一步最多等多久（500 到 10000 毫秒）。超时就放弃，照没打开挑选那样给出记忆块。"
        }
      },
      {
        "key": "afterthought",
        "kind": "boolean",
        "default": false,
        "label": {
          "en": "Recall again after replying",
          "zh": "回复之后再想一想"
        },
        "help": {
          "en": "Experimental. After each reply, look memories up once more in the background with the reply as the query, and give the ones this conversation has not had yet on the next turn, in a second block after the memory block. Costs one more lookup per turn; the added block makes the context longer, and it may hold memories unrelated to the new message. Under keyword retrieval the second lookup matches words of the reply. Needs modules.autoRecall.",
          "zh": "实验功能。每次回复之后，在后台拿这条回复再查一次记忆，把这个对话里还没给过的记忆在下一轮放进记忆块之后的第二个块里。每轮多一次查询；多出来的块会让上下文变长，里面可能有和新消息无关的记忆。用 keyword 检索时，第二次查询匹配的是回复里的词。需要 modules.autoRecall。"
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
          "zh": "再想一想最多加几条"
        },
        "help": {
          "en": "With advanced.afterthought on: the most memories one second lookup keeps for the next turn (1 to 5).",
          "zh": "回复之后再想一想打开时：一次再查最多留几条给下一轮（1 到 5）。"
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
          "zh": "实验功能。在内存里记住这个对话给过哪些记忆。宿主压缩了历史、那些记忆块不在了的时候，把仍在谈论中的几条再给一次，放在记忆块之后的第二个块里。一条记忆给出之后约五轮内算仍在谈论中，你的消息或模型的回复一再用到它则更久。多出来的块会让上下文变长，里面可能有和新消息无关的记忆。不写入磁盘。需要 modules.autoRecall。"
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
          "zh": "对话池一次带回几条"
        },
        "help": {
          "en": "With advanced.conversationPool on: the most memories the pool brings back in one turn (1 to 5).",
          "zh": "对话池打开时：一轮最多带回几条（1 到 5）。"
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
          "zh": "实验功能。新的对话开始时，在它的第一轮把上一个对话还在用的记忆给一次，放在记忆块之后的第二个块里。为此要在数据文件夹里放一个小文件（recent-pool.json），里面是这几条记忆。关掉这项会删除这个文件。多出来的块会让上下文变长，里面可能有和新消息无关的记忆。需要 modules.autoRecall。"
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
          "zh": "近期池一次带上几条"
        },
        "help": {
          "en": "With advanced.recentPool on: the most memories carried into a new conversation (1 to 5).",
          "zh": "近期池打开时：带进新对话的记忆最多几条（1 到 5）。"
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
          "zh": "近期池保留几天"
        },
        "help": {
          "en": "With advanced.recentPool on: a saved conversation older than this many days is not carried into a new one (1 to 30).",
          "zh": "近期池打开时：保存下来的对话超过这么多天，就不再带进新对话（1 到 30）。"
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
              "zh": "broad：每轮条数多，漏得少，杂讯也多"
            }
          },
          {
            "value": "precise",
            "label": {
              "en": "precise: about one memory per turn, closer matches, misses more",
              "zh": "precise：每轮约一条，更贴题，漏得也多"
            }
          }
        ],
        "label": {
          "en": "Recall profile",
          "zh": "召回档位"
        },
        "help": {
          "en": "How strict the gateway is about what counts as related. The memory block stays in the conversation, so broad makes the context grow faster.",
          "zh": "网关判断“相关”的严格程度。记忆块会留在对话里，broad 会让上下文涨得更快。"
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
          "zh": "网关每天最多收下几条记忆；0 表示不限。网关重启后重新计数。"
        }
      },
      {
        "key": "redaction",
        "kind": "boolean",
        "default": true,
        "label": {
          "en": "Redact secrets",
          "zh": "去掉秘密"
        },
        "help": {
          "en": "Remove secrets such as keys, tokens and passwords from memories when they are saved and when they are recalled.",
          "zh": "保存和召回记忆时去掉密钥、令牌、密码这类秘密。"
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
          "zh": "新的优先"
        },
        "help": {
          "en": "Among nearly equal matches, put newer memories first.",
          "zh": "相关度差不多时，较新的记忆排在前面。"
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
          "zh": "很久没用到的记忆排得更靠后，最终存档并删除（每天最多几条）。存档的记忆在数据文件夹里保留 90 天。"
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
