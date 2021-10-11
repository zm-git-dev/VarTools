import os
import pandas as pd
from multiprocessing import Pool
from script.common import execute_system, fastq_prework, get_raw_info, check_software


# 输入是包含一对（或多对）fastq文件的目录，文件名须以1/2.fq.gz或1/2.fastq.gz或1/2.fq或1/2.fastq结尾，同对文件前缀一致
# 输出为比对且处理以后的文件目录（新建，已存在则报错退出），及其报告（单独的目录）
def align_deal(indir,
               outdir,
               sample_name,
               reference,
               report_dir,
               tmp_dir,
               software,
               max_process,
               thread,
               script_path,
               bq,
               ver,
               bed,
               fmd):
    # mapping and sort
    indir, max_process, sample_list, state = fastq_prework(indir, max_process)
    # gatk_bundle_dir = gatk_bundle_dir.rstrip('/') + '/'
    tmp_dir = tmp_dir + '/' + sample_name
    # reference = os.path.abspath(reference)
    os.makedirs(tmp_dir)
    if max_process:
        process = Pool(max_process)
        for sample in sample_list:
            process.apply_async(single_align, args=(
                indir, tmp_dir, sample, reference, software, thread, state, script_path,))
        print('[ Msg: Waiting for all data in <%s> mapping done ... ]' % sample_name)
        process.close()
        process.join()

    else:
        for sample in sample_list:
            single_align(indir, tmp_dir, sample, reference, software, thread, state, script_path)
    print('[ Msg: All data in <%s> mapping done ! ]' % sample_name)
    # merge
    _merge_bam = tmp_dir + '/' + '*.sorted.bam'
    _merged_bam = tmp_dir + '/' + sample_name + '.sorted.merge.bam'
    if len(sample_list) > 1:
        merge_cmd = 'samtools merge -@ %d -c %s %s' % (thread, _merged_bam, _merge_bam)
    else:
        merge_cmd = 'mv %s %s' % (_merge_bam, _merged_bam)
    execute_system(merge_cmd, '[ Msg: Merge <%s> bam file done ! ]' % sample_name,
                   '[ E: Something wrong with merge <%s> bam file in mapping ! ]' % sample_name)
    rm_cmd = 'rm -f %s' % _merge_bam
    execute_system(rm_cmd, '[Msg: Delete <%s> process file done and begin to mark duplicate ... ]' % sample_name,
                   '[ E: Something wrong with delete <%s> process file after merge ! ]' % sample_name)
    # mark duplicate
    _duped_bam = outdir + '/' + sample_name + '.sorted.merge.markdup.bam'
    if fmd:
        dup_cmd = script_path + '/bin/sambamba markdup -t %d %s %s' % (thread, _merged_bam, _duped_bam)
        execute_system(dup_cmd, '[ Msg: <%s> mark duplication done ! ]' % sample_name,
                       '[ E: Something wrong with <%s> mark duplication ! ]' % sample_name)
    else:
        _dup_metrics = outdir + '/' + sample_name + '.markdup_metrics.txt'
        dup_cmd = script_path + '/bin/gatk/gatk MarkDuplicates ' \
                                '-INPUT %s -OUTPUT %s -METRICS_FILE %s' % (_merged_bam, _duped_bam, _dup_metrics)
        execute_system(dup_cmd, '[ Msg: <%s> mark duplication done ! ]' % sample_name,
                       '[ E: Something wrong with <%s> mark duplication ! ]' % sample_name)
        # index 使用sambamba会自动产生索引文件
        index_cmd = 'samtools index %s' % _duped_bam
        execute_system(index_cmd, '[ Msg: <%s> bam index done ! ]' % sample_name,
                       '[ E: Something wrong with index <%s> marked dup bam file ! ]' % sample_name)

    rm_cmd = 'rm -rf %s' % tmp_dir
    execute_system(rm_cmd, '[Msg: Delete <%s> process file done and begin to index ... ]' % sample_name,
                   '[ E: Something wrong with delete <%s> process file after mark duplicate ! ]' % sample_name)

    # # BQSR
    # _bqsr_table = outdir + sample_name + '.recal_data.table'
    # _bqsr_bam = outdir + sample_name + '.BQSR.bam'
    # # BaseRecalibrator
    # BR_cmd = './bin/gatk/gatk --java-options "-Xmx%dg -Xms%dg" BaseRecalibrator -R %s -I %s ' \
    #          '--known-sites %s1000G_phase1.indels.hg19.sites.vcf ' \
    #          '--known-sites %sMills_and_1000G_gold_standard.indels.hg19.sites.vcf ' \
    #          '--known-sites %sdbsnp_138.hg19.vcf -O %s' % (
    #              gatk_ram, gatk_ram, reference, _duped_bam, gatk_bundle_dir, gatk_bundle_dir, gatk_bundle_dir,
    #              _bqsr_table)
    # execute_system(BR_cmd, '[ Msg: <%s> baseRecalibrator done ! ]' % sample_name,
    #                '[ E: Something wrong with <%s> BaseRecalibrator ! ]' % sample_name)
    # # ApplyBQSRCmd
    # apply_BQSR_cmd = './bin/gatk/gatk --java-options "-Xmx%dg -Xms%dg" ApplyBQSR --bqsr-recal-file %s -R %s -I %s ' \
    #                  '-O %s' % (gatk_ram, gatk_ram, _bqsr_table, reference, _duped_bam, _bqsr_bam)
    # execute_system(apply_BQSR_cmd, '[ Msg: <%s> ApplyBQSR done ! ]' % sample_name,
    #                '[ E: Something wrong with <%s> ApplyBQSR ! ]' % sample_name)
    # # index
    # index_cmd = 'samtools index %s' % _bqsr_bam
    # execute_system(index_cmd, '[ Msg: <%s> BQSR bam index done ! ]' % sample_name,
    #                '[ E: Something wrong with <%s> BQSR bam index ! ]' % sample_name)

    # rm_cmd = 'rm -f %s' % _duped_bam
    # execute_system(rm_cmd, '[Msg: Delete <%s> process file done ! ]' % sample_name,
    #                '[ E: Something wrong with delete <%s> process file after BQSR ! ]' % sample_name)

    # stat result
    bam_stats(_duped_bam, report_dir, thread, tmp_dir, script_path, bq, ver, bed)
    return _duped_bam


def single_align(indir, outdir, sample, reference, software, thread, state, scriptPath):
    fastq1 = indir + '/' + sample
    if state == 'novo':
        # W2018005_NZTD180700064_H5MYLDSXX_L3_1.fq.gz
        fastq2 = indir + '/' + sample[:sample.rfind('1')] + '2' + sample[sample.rfind('1') + 1:]
        out_name = outdir + '/' + sample[:sample.rfind('1')].rstrip('_') + '.sorted.bam'
    else:
        # NA24695_CTTGTA_L002_R1_014.fastq.gz
        fastq2 = indir + '/' + sample[:sample.rfind('R1')] + 'R2' + sample[sample.rfind('R1') + 2:]
        out_name = outdir + '/' + sample[:sample.rfind('_R1')] + \
                   sample[sample.rfind('R1_') + 2:sample.find('.')] + '.sorted.bam'

    sample_name, _lb, _id = get_raw_info(fastq1, state)
    # 比对、转换、排序
    if software == 'bwa':
        bwa_mem2(fastq1, fastq2, out_name, reference, sample_name, thread, _id, _lb, scriptPath)
    elif software == 'bowtie2':
        bowtie2(fastq1, fastq2, out_name, reference, sample_name, thread, _id, _lb, scriptPath)
    elif software == 'gg':
        graph_genome(fastq1, fastq2, out_name, reference, sample_name, thread, _id, _lb)
    else:
        exit('[ E: Do not support <%s> to mapping ! ]' % software)


def bwa_mem2(fq1, fq2, _out_bam, reference, sample_name, thread, _id, _lb, scriptPath):
    # 检查索引文件
    file_name = os.path.basename(fq1).split('1')[0]
    suffix_list = ['.0123', '.amb', '.ann', '.bwt.2bit.64', '.bwt.8bit.32', '.pac']
    for suffix in suffix_list:
        if not os.path.exists(reference + suffix):
            print('[ Msg: Do not find bwa-mem2 index file of reference <%s> .]' % os.path.basename(reference))
            print('[ Msg: begin to build bwa-mem2 index file of reference ... ]')
            index_cmd = scriptPath + '/bin/bwa-mem2/bwa-mem2 index %s' % reference
            execute_system(index_cmd, '[ Msg: Build bwa-mem2 index file done ! ]',
                           '[ E: Fail to build bwa-mem2 index file of reference ! ]')
            break
    # 开始比对
    # _tmp_bam = _out_bam + '.tmp0'
    map_cmd = scriptPath + r'/bin/bwa-mem2/bwa-mem2 mem -t %d -M -R ' \
                           r'"@RG\tID:%s\tPL:ILLUMINA\tLB:%s\tSM:%s" %s %s %s | ' \
                           r'samtools sort -@ %d -m 4G - > %s' % (
                  thread, _id, _lb, sample_name, reference, fq1, fq2, thread, _out_bam)
    execute_system(map_cmd, '[ Msg: <%s> mapping and sort done ! ]' % file_name,
                   '[ E: <%s> fail to mapping with bwa-mem2 or sort bam file ! ]' % file_name)
    # 删除中间文件
    # rm_cmd = 'rm -f %s' % _tmp_bam
    # execute_system(rm_cmd, '[Msg: Delete <%s> process file done after mapping and sort !]' % file_name,
    #                '[ E: Fail delete <%s> process file after mapping and sort ! ]' % file_name)


def bowtie2(fq1, fq2, _out_bam, reference, sample_name, thread, _id, _lb, scriptPath):
    # 检查索引文件
    file_name = os.path.basename(fq1).split('1')[0]
    suffix_list = ['.1.bt2', '.2.bt2', '.3.bt2', '.4.bt2', '.rev.1.bt2', '.rev.2.bt2']
    for suffix in suffix_list:
        if not os.path.exists(reference + suffix):
            print('[ Msg: Do not find bowtie2 index file of reference <%s> .]' % os.path.basename(reference))
            print('[ Msg: begin to build bowtie2 index file of reference ... ]')
            index_cmd = scriptPath + '/bin/bowtie2-2.3.4-linux-x86_64/bowtie2-build %s %s' % (reference, reference)
            execute_system(index_cmd, '[ Msg: Build bowtie2 index file done ! ]',
                           '[ E: Fail to build bowtie2 index file of reference ! ]')
            break
    # 开始比对
    _tmp_raw_sam = _out_bam + '.tmp0'
    _tmp_uniq_sam = _out_bam + '.tmp1'
    # _tmp_bam = _out_bam + '.tmp2'
    map_cmd = scriptPath + r'/bin/bowtie2 -p %d -x %s --no-unal --rg-id %s --rg PL:ILLUMINA --rg LB:%s --rg SM:%s ' \
                           r'-1 %s -2 %s -S %s && grep -v "XS:" %s > %s ' \
                           r'&& samtools view -q 1 -Shb %s | samtools sort -@ %d -m 4G - > %s' \
              % (thread, reference, _id, _lb, sample_name, fq1, fq2, _tmp_raw_sam,
                 _tmp_raw_sam, _tmp_uniq_sam, _tmp_uniq_sam, thread, _out_bam)
    execute_system(map_cmd, '[ Msg: <%s> mapping and sort done ! ]' % file_name,
                   '[ E: <%s> fail to mapping with bowtie2 or sort bam file ! ]' % file_name)
    # 删除中间文件
    rm_cmd = 'rm -f %s.tmp?' % _out_bam
    execute_system(rm_cmd, '[Msg: Delete <%s> process file done after mapping and sort !]' % file_name,
                   '[ E: Fail delete <%s> process file after mapping and sort ! ]' % file_name)


def graph_genome(fq1, fq2, _out_bam, reference, sample_name, thread, _id, _lb):
    file_name = os.path.basename(fq1).split('1')[0]
    input_dir = os.path.dirname(os.path.abspath(fq1))
    reference_dir = os.path.dirname(os.path.abspath(reference))
    out_dir = os.path.dirname(os.path.abspath(_out_bam))
    reference_basename = os.path.basename(reference)
    _fq1 = os.path.basename(fq1)
    _fq2 = os.path.basename(fq2)
    _bam = os.path.basename(_out_bam)
    _tmp_bam = _bam + '.tmp0'
    map_cmd = 'docker run -v "%s":"/input" -v "%s":"/ref" -v "%s":"/out" gral-bpa:"0.9.1" /usr/local/bin/aligner ' \
              '--vcf /ref/SBG.Graph.B37.V6.rc6.vcf.gz --reference /ref/%s -q /input/%s -Q /input/%s -o /out/%s ' \
              '--read_group_sample "%s" --read_group_library "%s" --read_group_id "%s" --threads %d && ' \
              'samtools sort -@ %d -m 4G -O bam -o %s %s' % (
                  input_dir, reference_dir, out_dir, reference_basename, _fq1, _fq2, _tmp_bam, sample_name, _lb, _id,
                  thread, thread, _out_bam, out_dir + '/' + _tmp_bam)
    execute_system(map_cmd, '[ Msg: <%s> mapping and sort done ! ]' % file_name,
                   '[ E: <%s> fail to mapping with graph genome or sort bam file ! ]' % file_name)
    # 删除中间文件
    rm_cmd = 'rm -f %s' % _tmp_bam
    execute_system(rm_cmd, '[Msg: Delete <%s> process file done after mapping and sort !]' % file_name,
                   '[ E: Fail delete <%s> process file after mapping and sort ! ]' % file_name)


def bam_stats(_duped_bam, report_dir, thread, tmp_dir, script_path, bq, ver, bed):
    if bq:
        rst = check_software('qualimap')
        if rst:
            print('[W: Can not find qualimap. Skipping the bam quality check with qualimap ! ]')
        else:
            bamqc_cmd = 'qualimap bamqc --java-mem-size=20G -bam %s -c -nt %d -outdir %s -outformat PDF:HTML' % (
                _duped_bam, thread, report_dir)
            rst = os.system(bamqc_cmd)
            if rst:
                print('[ E: fail to bam QC with qualimap! ]')
            else:
                print('[ Msg: qualimap bam QC done ! ]')
    if not bed and ver == 'hg38':
        bedfile = script_path + '/lib/Hg38.genome.bed'
    elif not bed and ver == 'hg19':
        bedfile = script_path + '/lib/Hg19.genome.bed'
    else:
        bedfile = bed
    bam_qc(_duped_bam, report_dir, thread, tmp_dir, script_path, bedfile)


def bam_qc(bam, report_dir, tmp_dir, script_path, thread, bedfile):
    base_cov, _start, end = 0, 0, 0
    _chr = ['chr1', '1']
    bamqc_dict = {}
    # 目标区大小
    new_target_file = open(tmp_dir + '/tmp_target.txt', 'w')
    with open(bedfile) as f:
        for line in f:
            record = line.split('\t')
            if eval(record[1]) > eval(record[2]):
                print('Error: The positions are not in chromosomal order (%s:%s comes after %s)' % (
                    record[0], record[2], record[1]))
                new_target_file.close()
                return 0
            new_target_file.write('\t'.join([record[0], str(eval(record[1]) + 1), record[2]]) + '\n')
            if record[0] in _chr and eval(record[1]) <= end:
                if _start >= eval(record[1]):
                    print('Error: The positions are not in chromosomal order (%s:%s comes after %s)'
                          % (record[0], _start, record[1]))
                    new_target_file.close()
                    return 0
                base_cov = base_cov - (end - eval(record[1]))
            base_cov += eval(record[2]) - eval(record[1])
            end = eval(record[2])
            _start = eval(record[1])
            _chr = record[0]
    new_target_file.close()
    bamqc_dict['base_cov'] = base_cov
    # 比对率、平均测序深度
    single_bam_qc(bam, bedfile, tmp_dir, report_dir, 'tmp_target.txt', script_path, thread)
    os.system('rm -rf %s' % tmp_dir)
    print('[ Msg: Bam QC done！]')


def single_bam_qc(bam, bedfile, tmp_dir, report_dir, new_target_file, script_path, thread):
    result_dict = {
        'target_dep_num': 0,
        'dep_1x': 0,
        'dep_10x': 0,
        'dep_20x': 0,
        'dep_30x': 0,
        'mapping_rate': 0
    }
    # flagstat
    flag_cmd = 'samtools flagstat -@ %d %s > %s' % (thread, bam, report_dir + '/flagstat.txt')
    rst = os.system(flag_cmd)
    if rst:
        print('[ Error: Something wrong with bam flagstat ! ]')
    else:
        print('[ Msg: bam flagstat done ! ]')
        with open(report_dir + '/flagstat.txt') as f:
            for line in f:
                if line.find('mapped (') != -1:
                    mapping_rate = line.split('(')[-1].split(':')[0].strip()
                    result_dict['mapping_rate'] = mapping_rate
                    break
    # stats
    stats_cmd = 'samtools stats -d -@ %d -t %s %s > %s' % (thread, new_target_file, bam, report_dir + '/stats.txt')
    rst = os.system(stats_cmd)
    if rst:
        print('[ Error: Something wrong with bam stats ! ]')
    else:
        print('[ Msg: bam stats done ! ]')
        with open(report_dir + '/stats.txt') as f:
            for i in f:
                if i.startswith('SN') and i.find('bases mapped:') != -1:
                    result_dict['target_dep'] = eval(i.split(':')[-1].split('#')[0].strip())
    # coverage
    depth_cmd = script_path + '/bin/mosdepth -n -t %d -b %s -T 1,10,20,30 %s %s' % (
        thread, bedfile, tmp_dir + '/' + bam.split('/')[-1].rstrip('.bam'), bam)
    rst = os.system(depth_cmd)
    if rst:
        print('[ Error: Something wrong with bam stat depth ! ]')
    else:
        print('[ Msg: bam stat depth done ! ]')
        data = pd.read_table(tmp_dir + '/' + bam.split('/')[-1].rstrip('.bam') + '.thresholds.bed.gz',
                             low_memory=False,
                             compression='gzip')
        sum_end_start = sum(data['end'] - data['start'])
        sum_1X = sum(data['1X'])
        sum_10X = sum(data['10X'])
        sum_20X = sum(data['20X'])
        sum_30X = sum(data['30X'])
        p1 = sum_1X / sum_end_start
        p10 = sum_10X / sum_end_start
        p20 = sum_20X / sum_end_start
        p30 = sum_30X / sum_end_start
        result_dict['dep_1x'] = p1
        result_dict['dep_10x'] = p10
        result_dict['dep_20x'] = p20
        result_dict['dep_30x'] = p30

        df = pd.read_table(tmp_dir + '/' + bam.split('/')[-1].rstrip('.bam') + '.mosdepth.summary.txt')
        df = df[df['chrom'].isin(
            ['chr1', 'chr2', 'chr3', 'chr4', 'chr5', 'chr6', 'chr7', 'chr8', 'chr9', 'chr10', 'chr11', 'chr12', 'chr13',
             'chr14', 'chr15', 'chr16', 'chr17', 'chr88', 'chr19', 'chr20', 'chr21', 'chr22', 'chrX', 'chrY'])]
        result_dict['target_dep_num'] = sum(df['bases'])
    # 区域总base 太费时了
    # bed_cov_list = []
    # bedcov_cmd = 'samtools bedcov %s %s > %s' % (bedfile, bam, report_dir + '/bedcov.txt')
    # rst = os.system(bedcov_cmd)
    # if rst:
    #     print('[ Error: Something wrong with bam bedcov! ]')
    # else:
    #     print('[ Msg: bam bedcov done ! ]')
    #     with open(report_dir + '/bedcov.txt') as f:
    #         for i in f:
    #             bed_cov_list.append(eval(i.split('\t')[-1]))
    #         result_dict['target_dep_num'] = sum(bed_cov_list)
    # total_dep_num 太耗时
    # stats_all_cmd = 'samtools stats -d -@ %d %s > %s' % (thread, bam, tmp_dir + '/stats_arst.tmp')
    # rst = os.system(stats_all_cmd)
    # if rst:
    #     print('[ Error: Something wrong with bam stats all ! ]')
    # else:
    #     print('[ Msg: bam stats all done ! ]')
    #     with open(tmp_dir + 'stats_arst.tmp') as f:
    #         for i in f:
    #             if i.startswith('SN') and i.find('bases mapped:') > 0:
    #                 result_dict['total_dep_num'] = eval(i.split(':')[-1].split('#')[0].strip())
    fo = open(report_dir + '/Bam_QC.txt', 'w')
    for k, v in result_dict.items():
        fo.write(k + '\t' + str(v) + '\n')
    fo.close()


def ref_n_counts(reference, script_path):
    # 计算基因组每条染色体N的个数, 输出是统计覆盖率要用的，存在于当前目录下的lib目录中
    N_count = {}
    _chr = ''
    with open(reference) as f:
        for line in f:
            if line.startswith('>'):
                _chr = line.lstrip('>').rstrip().split('\t')[0].split(' ')[0]
            else:
                N_count[_chr] = line.count('N') + N_count.get(_chr, 0)
    n_file = open(script_path + '/lib/' + reference + '.N.txt', 'w')
    for k, v in N_count.items():
        n_file.write(k + '\t' + str(v) + '\n')
    n_file.close()
    print('[ Msg: N number file created done !]')


def n_region(fasta):
    from Bio import SeqIO
    bed_list = []
    # import the SeqIO module from Biopython
    with open(fasta) as fasta_handle:
        for record in SeqIO.parse(fasta_handle, "fasta"):
            start_pos, counter, gap, gap_length = 0, 0, False, 0
            for char in record.seq:
                if char in ['N', 'n']:
                    if gap_length == 0:
                        start_pos = counter
                        gap_length = 1
                        gap = True
                    else:
                        gap_length += 1
                else:
                    if gap:
                        bed_list.append(record.id + "\t" + str(start_pos) + "\t" + str(start_pos + gap_length))
                        gap_length = 0
                        gap = False
                counter += 1
    return bed_list
