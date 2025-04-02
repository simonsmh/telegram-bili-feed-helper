import re
import httpx
import asyncio
from tqdm import tqdm

async def get_video_playurl():
    url = 'https://api.bilibili.com/x/player/playurl'
    params = {
        "avid": "883362563",
        "cid": "196018899",
        "qn": "125",
        "fnval": "4048",
        "fourk": "1",
        "fnver": "0",
    }
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Referer': 'https://www.bilibili.com'
    }

    async with httpx.AsyncClient() as client:
        # 获取视频信息
        response = await client.get(url, params=params, headers=headers)
        response.raise_for_status()
        data = response.json()
        
        # 获取视频流URL
        video_url = data['data']['dash']['video'][0]['baseUrl']
        print(f"原始视频流URL: {video_url}")
        
        # 替换域名
        video_url = re.sub(r'https?://[^/]+/', 'https://upos-sz-mirroraliov.bilivideo.com/', video_url)
        # 替换带宽参数
        video_url = re.sub(r"&bw=\d+&", "&bw=12800000&", video_url)
        
        print(f"替换域名后的URL: {video_url}")
        
        # 使用流式请求获取视频内容
        async with client.stream('GET', video_url, headers=headers) as stream:
            # 获取文件总大小
                    unit='iB',
                    unit_scale=True,
                    unit_divisor=1024,
                    desc='下载视频'
                ) as progress:
                    async for chunk in stream.aiter_bytes():
                        size = f.write(chunk)
                        progress.update(size)
        
        return data

if __name__ == '__main__':
    asyncio.run(get_video_playurl())