import os.path as osp
import sys
import cv2
import argparse
import numpy as np
import subprocess
import time
import re
__proot__ = osp.normpath(osp.join(osp.dirname(__file__), ".."))
sys.path.append(__proot__)

from worker.worker3 import Worker3
def make_parser():
    parser = argparse.ArgumentParser("simple block work flow")
    parser.add_argument(
        "-vin", "--video_input",
        type=str, required=True,
        help="source to worker"
    )
    parser.add_argument(
        "-c", "--config",
        type=str, required=True,
        help="YAML format configuration to worker"
    )
    parser.add_argument(
        "-fl", "--frame_limit",
        type=int, default=-1,
        help="stop after frame amount"
    )
    parser.add_argument(
        "-vout", "--video_output",
        type=str, default=None,
        help="store worker visualize result"
    )
    parser.add_argument(
        "-vlog", "--vout_log",
        type=str, default="video_storing.log",
        help="log when store video result with ffmpeg"
    )
    parser.add_argument(
        "-so", "--stream_output",
        type=str, default=None,
        help="media server location for pushing stream with ffmpeg"
    )
    parser.add_argument(
        "-slog", "--stream_log",
        type=str, default="stream_pushing.log",
        help="log when pushing stream with ffmpeg"
    )
    parser.add_argument(
        "-s", "--output_scale",
        type=str, default=None,
        help="scaling of output stream and stored file"
    )
    parser.add_argument(
        "-en", "--output_encoder",
        type=str, default="h264_nvenc",
        help="storing and pushing stream video encoder"
    )
    parser.add_argument(
        "--track_thresh",
        type=float, default=0.5,
        help="tracking confidence threshold"
    )
    parser.add_argument(
        "--track_buffer", 
        type=int, default=30,
        help="the frames for keep lost tracks"
    )
    parser.add_argument(
        "--match_thresh", 
        type=float, default=0.8,
        help="matching threshold for tracking"
    )
    parser.add_argument(
        '--reid', 
        action='store_true',
        help='using reid module to provide appearance features'
    )
    return parser

if __name__ == "__main__":
    args = make_parser().parse_args()
    track_parameter = {
        "track_thresh":args.track_thresh,
        "track_buffer":args.track_buffer,
        "match_thresh":args.match_thresh,
        "aspect_ratio_thresh":1.6,
        "min_box_area":10.0,
        "mot20":False,
    }

    test_size = 11
    frame_buffer = []
    # framerate estimate
    cap = cv2.VideoCapture(args.video_input)
    if not cap.isOpened():
        raise Exception(f'Could not open file "{args.video_input}"!')
    actual_framerate = None
    for i in range(test_size):
        ret, frame = cap.read()
        frame_buffer.append(frame)
        if i == 0:
            start = (
                cap.get(cv2.CAP_PROP_POS_FRAMES),
                cap.get(cv2.CAP_PROP_POS_MSEC)
            )
        if i == test_size - 1:
            p = cap.get(cv2.CAP_PROP_POS_FRAMES)
            t = cap.get(cv2.CAP_PROP_POS_MSEC)
            actual_framerate = (p-start[0]) / (t-start[1]) * 1000.0
    cap.release()
    print(f'estimate framerate is : {actual_framerate}')

    worker = Worker3(
        args.video_input,
        args.config, track_parameter,
        actual_framerate = actual_framerate,
        reid=args.reid
    )

    source_size = (
        int(worker.cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
        int(worker.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    )
    print(f'source_size : {source_size}')
    stored_size = source_size
    if args.output_scale is not None:
        try:
            if re.match(
                "^\(([1-9]\d*|-1)\):\(([1-9]\d*|-1)\)$",
                args.output_scale
            ) is None:
                raise
            size = args.output_scale.split(':')
            if size[0] == size[1] == '(-1)':
                raise
            stored_size = (int(size[0][1:-1]), int(size[1][1:-1]))
            if stored_size[0] == -1:
                ratio = stored_size[1] / source_size[1]
                stored_size = (
                    int(source_size[0] * ratio),
                    stored_size[1]
                )
            elif stored_size[1] == -1:
                ratio = stored_size[0] / source_size[0]
                stored_size = (
                    stored_size[0],
                    int(source_size[1] * ratio)
                )
            print(f"stored_size : {stored_size}")
        except:
            raise ValueError('The strings specified for scaling size are not correct. Please use "(width):(height)". And you can specify one of them to be -1 to automatically scale, but not both.')
    shouldResize = source_size != stored_size

    # worker_analysis
    round_times = np.zeros(test_size)
    frame_buffer = []
    worker_iter = iter(worker)
    for i in range(11):
        t0 = time.time()
        fid, frame = next(worker_iter)
        if shouldResize:
            frame = cv2.resize(frame, stored_size)
        round_times[i] = time.time() - t0
        frame_buffer.append((fid, frame))
    worker_process_rate = 1.0 / round_times[1:].mean()


    if args.video_output is None:
        vwriter_process = None
    else:
        command = ['ffmpeg',
            '-y', '-an',
            '-f', 'rawvideo',
            '-vcodec','rawvideo',
            '-pix_fmt', 'bgr24',
            '-s', f'{stored_size[0]}x{stored_size[1]}',
            '-r', f'{worker.fps}',
            '-i', '-',
            '-vcodec', args.output_encoder,
            args.video_output
        ]
        vwriter_logFile = open(args.vout_log, 'w')
        vwriter_process = subprocess.Popen(
            command, shell=False, 
            stdin=subprocess.PIPE,
            stdout=vwriter_logFile, stderr=vwriter_logFile
        )
    
    if args.stream_output is None:
        ffmpeg_process = None
    else:
        command = ['ffmpeg',
            '-y', '-an',
            '-f', 'rawvideo',
            '-vcodec','rawvideo',
            '-pix_fmt', 'bgr24',
            '-s', f'{stored_size[0]}x{stored_size[1]}',
            '-r', f'{worker_process_rate}',
            '-i', '-',
            '-vcodec', args.output_encoder,
            '-f', 'rtsp',
            args.stream_output
        ]
        stream_logFile = open(args.stream_log, 'w')
        ffmpeg_process = subprocess.Popen(
            command, shell=False, 
            stdin=subprocess.PIPE,
            stdout=stream_logFile, stderr=stream_logFile
        )
    
    print(f'FPS : {worker_process_rate}')
    for fid, frame in frame_buffer:
        print(f"Now is => [ {fid / worker.fps} ]", end='\r')
        if ffmpeg_process is not None:
            ffmpeg_process.stdin.write(frame.data.tobytes())
        if vwriter_process is not None:
            vwriter_process.stdin.write(frame.data.tobytes())
    print('\nstart working!!!')
    
    try:
        for fid, frame in worker:
            print(f"Now is => [ {fid / worker.fps} ]", end='\r')
            if shouldResize:
                frame = cv2.resize(frame, stored_size)
            frameBytes = frame.data.tobytes()
            if ffmpeg_process is not None:
                ffmpeg_process.stdin.write(frameBytes)
            if vwriter_process is not None:
                vwriter_process.stdin.write(frameBytes)
            if args.frame_limit > 1 and fid >= args.frame_limit:
                break
        print('\n')
    except KeyboardInterrupt:
        worker._endingWork()
        print("\nInterupt...Shutdown...ok...")
    except:
        raise
    finally:
        if args.stream_output is not None:
            stream_logFile.close()
        if args.video_output is not None:
            vwriter_logFile.close()
        if ffmpeg_process is not None:
            ffmpeg_process.terminate()
        if vwriter_process is not None:
            vwriter_process.terminate()
